import json
import os
import re
import time
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation

import logging
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db import SessionLocal, get_db
from app.logging import configure_logging, get_logger, log_event
from app.models import (
    AgentEvent,
    AgentRun,
    ChatMessage,
    ChecklistItem,
    ItineraryDay,
    ItineraryItem,
    Place,
    Trip,
    TripPlace,
    User,
    utc_now,
)
from app.schemas import (
    AgentEventRead,
    ChatMessageCreate,
    ChatMessageRead,
    ChecklistItemRead,
    ItineraryDayCreate,
    ItineraryDayRead,
    ItineraryItemPatch,
    ItineraryItemRead,
    PlaceCreate,
    TripCreate,
    TripPatch,
    TripPlacePatch,
    TripPlaceRead,
    TripRead,
    UserRead,
)

configure_logging()
logger = get_logger("wayfinder.api")

app = FastAPI(title="Wayfinder OS")

frontend_origin = os.getenv("FRONTEND_ORIGIN", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin] if frontend_origin != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def structured_request_logging(request, call_next):
    start = time.perf_counter()
    response = None

    try:
        response = await call_next(request)
        return response
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        log_event(
            logger,
            logging.INFO,
            "request.completed",
            method=request.method,
            path=request.url.path,
            status_code=getattr(response, "status_code", 500),
            duration_ms=duration_ms,
        )

BETA_USER_EMAIL = os.getenv("BETA_USER_EMAIL", "beta@wayfinder.test")
BETA_USER_NAME = os.getenv("BETA_USER_NAME", "Beta Tester")

CHAT_ROLES = {"user", "assistant", "system"}
TRIP_PLACE_STATUSES = {"suggested", "interested", "booked", "skipped"}


class TravelQuery(BaseModel):
    query: str


class TripChatRequest(BaseModel):
    message: str = Field(min_length=1)


class BuildTripBudgetCategory(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    amount: float = Field(ge=0)


class BuildTripBudget(BaseModel):
    currency: str = Field(default="GBP", min_length=3, max_length=3)
    total_estimate: float | None = Field(default=None, ge=0)
    notes: list[str] = []
    categories: list[BuildTripBudgetCategory] = []


class BuildTripPlace(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    category: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    reason: str | None = None


class BuildTripItineraryItem(BaseModel):
    title: str = Field(min_length=1, max_length=220)
    description: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    category: str | None = Field(default=None, max_length=80)
    place_name: str | None = None
    is_locked: bool = False
    is_booked: bool = False
    sort_order: int = 0


class BuildTripItineraryDay(BaseModel):
    day_number: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=180)
    summary: str | None = None
    items: list[BuildTripItineraryItem] = []


class BuildTripChecklistItem(BaseModel):
    title: str = Field(min_length=1, max_length=220)
    description: str | None = None
    due_label: str | None = Field(default=None, max_length=120)
    priority: str | None = Field(default="medium", max_length=40)


class BuildTripOutput(BaseModel):
    trip_summary: str = Field(min_length=1)
    assumptions: list[str] = []
    warnings: list[str] = []
    budget: BuildTripBudget | None = None
    places: list[BuildTripPlace] = []
    itinerary: list[BuildTripItineraryDay] = []
    checklist: list[BuildTripChecklistItem] = []


SYSTEM_PROMPT = """
You are Wayfinder OS, a travel planning assistant.
Only answer travel-related questions.

Travel-related topics include destinations, itineraries, budgets, places to visit,
food, hotels, transport, flights, packing, safety, visas, seasons, and trip planning.

If the user query is not travel-related, reply exactly:
I can help with travel planning. Please send a question about destinations, itineraries, budgets, places to visit, food, hotels, transport, or trip planning.

If it is travel-related, provide a helpful travel guide with:
- a short summary
- a practical day-by-day plan when relevant
- budget notes
- transport tips
- 3 to 5 concrete recommendations

Keep the answer complete but concise.
"""

TRIP_AWARE_SYSTEM_PROMPT = """
You are Wayfinder OS, a travel planning assistant inside a specific trip workspace.
Only answer travel-related questions.

Use the trip context to resolve short follow-up messages. Do not ask for information
that already exists in context. If the user provides new details, incorporate them
into the answer. If the user changes constraints, explain the likely impact on the
itinerary, places, transport, or budget.

Keep responses concise, practical, and grounded in the current trip. Do not claim to
have changed the itinerary, places, or budget unless an explicit tool or endpoint has
actually made that change.
"""

BUILD_TRIP_SYSTEM_PROMPT = """
You are Wayfinder OS running the Build My Trip workflow.
You turn trip context and conversation into durable travel-planning artifacts.

Return only one valid JSON object matching this shape:
{
  "trip_summary": "short user-facing summary",
  "assumptions": ["short assumption"],
  "warnings": ["short warning"],
  "budget": {
    "currency": "GBP",
    "total_estimate": 500,
    "notes": ["short note"],
    "categories": [{"name": "Transport", "amount": 80}]
  },
  "places": [
    {
      "name": "Place name",
      "category": "Museum",
      "city": "City",
      "country": "Country",
      "reason": "Why it fits"
    }
  ],
  "itinerary": [
    {
      "day_number": 1,
      "title": "Day title",
      "summary": "Day summary",
      "items": [
        {
          "title": "Activity title",
          "description": "Useful concise detail",
          "start_time": "09:30",
          "end_time": "10:00",
          "category": "Transit",
          "place_name": null,
          "is_locked": false,
          "is_booked": false,
          "sort_order": 1
        }
      ]
    }
  ],
  "checklist": [
    {
      "title": "Task title",
      "description": "Task detail",
      "due_label": "Before departure",
      "priority": "medium"
    }
  ]
}

Use existing locked or booked itinerary items as constraints. Do not contradict them.
If origin, duration, budget, dates, or preferences are known, use them.
Keep the plan practical and specific to the destination.
Use null for unknown optional fields. Do not include markdown.
"""


def ndjson(event: dict) -> str:
    return json.dumps(event) + "\n"


def get_event_error_message(event) -> str:
    error = getattr(event, "error", None)
    if error and getattr(error, "message", None):
        return error.message

    message = getattr(event, "message", None)
    if message:
        return message

    return "Wayfinder could not complete this request. Please try again."


def format_date_value(value) -> str:
    return value.isoformat() if value else "not set"


def format_time_value(value) -> str:
    return value.strftime("%H:%M") if value else "TBD"


def clean_extracted_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip(" .,!?:;")).strip()


def extract_trip_context_updates(text: str) -> dict:
    updates: dict = {}

    origin_match = re.search(
        r"\b(?:coming|travelling|traveling|leaving|starting)\s+from\s+([A-Z][A-Za-z .'-]{1,60})",
        text,
    )
    if origin_match:
        origin = clean_extracted_text(origin_match.group(1))
        origin = re.split(r"\s+(?:for|on|with|and|but)\s+", origin, maxsplit=1)[0]
        if origin:
            updates["origin"] = origin

    budget_match = re.search(
        r"\b(?:my\s+)?budget(?:\s+is|\s+of|:)?\s*([£$€]\s?\d[\d,]*(?:\.\d{1,2})?)",
        text,
        re.IGNORECASE,
    )
    if budget_match:
        updates["budget"] = clean_extracted_text(budget_match.group(1))

    duration_match = re.search(r"\b(?:for|going for|we are going for)\s+(\d+)\s+days?\b", text, re.IGNORECASE)
    if duration_match:
        updates["duration"] = f"{duration_match.group(1)} days"

    avoid_match = re.search(r"\bavoid\s+([^.!?]+)", text, re.IGNORECASE)
    if avoid_match:
        constraint = clean_extracted_text(f"avoid {avoid_match.group(1)}")
        if constraint:
            updates.setdefault("constraints", []).append(constraint)

    preference_match = re.search(r"\b(?:want|prefer|interested in|like)\s+([^.!?]+)", text, re.IGNORECASE)
    if preference_match:
        preference = clean_extracted_text(preference_match.group(1))
        if preference and len(preference) <= 80:
            updates.setdefault("preferences", []).append(preference)

    return updates


def append_unique(values: list[str], new_values: list[str]) -> list[str]:
    normalized = {value.lower(): value for value in values}
    for value in new_values:
        key = value.lower()
        if key not in normalized:
            normalized[key] = value
    return list(normalized.values())


def merge_planning_context(existing: dict | None, updates: dict) -> dict:
    context = dict(existing or {})

    for key in ("origin", "budget", "duration"):
        if updates.get(key):
            context[key] = updates[key]

    if updates.get("duration"):
        context["known_details"] = append_unique(context.get("known_details", []), [updates["duration"]])

    for key in ("constraints", "preferences"):
        if updates.get(key):
            context[key] = append_unique(context.get(key, []), updates[key])

    return context


def summarize_itinerary(days: list[ItineraryDay]) -> list[str]:
    if not days:
        return ["No itinerary days saved yet."]

    lines = []
    for day in days[:8]:
        day_title = day.title or f"Day {day.day_number}"
        item_bits = []
        for item in day.items[:8]:
            flags = []
            if item.is_locked:
                flags.append("locked")
            if item.is_booked:
                flags.append("booked")
            flag_text = f" ({', '.join(flags)})" if flags else ""
            item_bits.append(f"{format_time_value(item.start_time)} {item.title}{flag_text}")
        items_text = "; ".join(item_bits) if item_bits else "No saved items"
        lines.append(f"Day {day.day_number}: {day_title} - {items_text}")

    return lines


def summarize_places(places: list[TripPlace]) -> list[str]:
    if not places:
        return ["No saved places yet."]

    grouped: dict[str, list[str]] = {}
    for trip_place in places:
        place = trip_place.place
        detail = place.name
        meta = ", ".join(part for part in (place.category, place.city, place.country) if part)
        if meta:
            detail = f"{detail} ({meta})"
        if trip_place.notes:
            detail = f"{detail}: {trip_place.notes}"
        grouped.setdefault(trip_place.status, []).append(detail)

    lines = []
    for status in sorted(grouped):
        lines.append(f"{status}: {'; '.join(grouped[status][:10])}")
    return lines


def summarize_locked_booked(days: list[ItineraryDay]) -> list[str]:
    items = []
    for day in days:
        for item in day.items:
            if item.is_locked or item.is_booked:
                flags = []
                if item.is_locked:
                    flags.append("locked")
                if item.is_booked:
                    flags.append("booked")
                items.append(f"Day {day.day_number}: {item.title} ({', '.join(flags)})")
    return items or ["No locked or booked itinerary items yet."]


def build_trip_context_prompt(
    trip: Trip,
    recent_messages: list[ChatMessage],
    days: list[ItineraryDay],
    places: list[TripPlace],
) -> str:
    planning_context = trip.planning_context or {}
    recent_history = [
        f"{message.role}: {message.content[:700]}"
        for message in recent_messages[-12:]
        if message.role in CHAT_ROLES
    ]
    missing_details = []
    if not planning_context.get("origin"):
        missing_details.append("origin not yet known")
    if not trip.start_date and not trip.end_date and not planning_context.get("duration"):
        missing_details.append("dates or duration not yet known")
    if not trip.budget_amount and not planning_context.get("budget"):
        missing_details.append("budget not yet known")

    return "\n".join(
        [
            "Trip workspace context:",
            f"- Title: {trip.title}",
            f"- Destination: {trip.destination}",
            f"- Start date: {format_date_value(trip.start_date)}",
            f"- End date: {format_date_value(trip.end_date)}",
            f"- Status/progress: {trip.status}, {trip.progress}% ready",
            f"- Budget: {trip.budget_amount if trip.budget_amount is not None else planning_context.get('budget', 'not set')}",
            f"- Planning context JSON: {json.dumps(planning_context, default=str)}",
            "",
            "Recent chat history:",
            *(recent_history or ["No prior chat messages."]),
            "",
            "Current itinerary summary:",
            *summarize_itinerary(days),
            "",
            "Current trip places grouped by status:",
            *summarize_places(places),
            "",
            "Locked/booked itinerary items:",
            *summarize_locked_booked(days),
            "",
            "Known assumptions or missing details:",
            *(missing_details or ["No obvious missing basics."]),
        ]
    )


def build_structured_trip_prompt(
    trip: Trip,
    recent_messages: list[ChatMessage],
    days: list[ItineraryDay],
    places: list[TripPlace],
) -> str:
    return "\n".join(
        [
            build_trip_context_prompt(trip, recent_messages, days, places),
            "",
            "Build a complete trip plan now.",
            "Persistable artifact requirements:",
            "- Generate itinerary days/items for the trip duration if known.",
            "- Include transport guidance from the known origin when present.",
            "- Recommend concrete places in the destination.",
            "- Include budget notes and category estimates.",
            "- Include booking/checklist tasks.",
            "- Keep locked/booked saved itinerary items respected as immovable constraints.",
        ]
    )


def parse_json_object(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def get_response_text(response) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return output_text

    chunks = []
    for output in getattr(response, "output", []) or []:
        for content in getattr(output, "content", []) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(text)
    return "".join(chunks)


def parse_time_string(value: str | None):
    if not value:
        return None

    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def normalize_place_key(name: str, city: str | None, country: str | None) -> tuple[str, str, str]:
    return (name.strip().lower(), (city or "").strip().lower(), (country or "").strip().lower())


def get_or_create_generated_place(db: Session, place_data: BuildTripPlace) -> Place:
    name = place_data.name.strip()
    city = place_data.city.strip() if place_data.city else None
    country = place_data.country.strip() if place_data.country else None

    place = db.scalar(
        select(Place).where(
            func.lower(Place.name) == name.lower(),
            func.coalesce(func.lower(Place.city), "") == (city or "").lower(),
            func.coalesce(func.lower(Place.country), "") == (country or "").lower(),
        )
    )
    if place:
        if place_data.category and not place.category:
            place.category = place_data.category
        return place

    place = Place(
        name=name,
        category=place_data.category,
        city=city,
        country=country,
    )
    db.add(place)
    db.flush()
    return place


def link_place_to_trip(
    db: Session,
    *,
    trip_id: str,
    place: Place,
    notes: str | None = None,
    priority: int | None = None,
) -> TripPlace:
    trip_place = db.scalar(select(TripPlace).where(TripPlace.trip_id == trip_id, TripPlace.place_id == place.id))
    if trip_place:
        if notes and not trip_place.notes:
            trip_place.notes = notes
        if priority is not None and trip_place.priority is None:
            trip_place.priority = priority
        return trip_place

    trip_place = TripPlace(
        trip_id=trip_id,
        place_id=place.id,
        status="suggested",
        notes=notes,
        priority=priority,
    )
    db.add(trip_place)
    db.flush()
    return trip_place


def replace_generated_itinerary(
    db: Session,
    *,
    trip_id: str,
    generated_days: list[BuildTripItineraryDay],
    places_by_key: dict[tuple[str, str, str], Place],
) -> tuple[int, int, int]:
    existing_days = db.scalars(
        select(ItineraryDay)
        .where(ItineraryDay.trip_id == trip_id)
        .options(selectinload(ItineraryDay.items))
        .order_by(ItineraryDay.day_number.asc())
    ).all()

    preserved_by_day: dict[str, int] = {}
    preserved_count = 0
    for day in existing_days:
        preserved_for_day = 0
        for item in list(day.items):
            if item.is_locked or item.is_booked:
                preserved_count += 1
                preserved_for_day += 1
            else:
                db.delete(item)
        preserved_by_day[day.id] = preserved_for_day

    db.flush()

    for day in list(existing_days):
        if preserved_by_day.get(day.id, 0) == 0:
            db.delete(day)

    db.flush()

    days_by_number = {
        day.day_number: day
        for day in db.scalars(
            select(ItineraryDay)
            .where(ItineraryDay.trip_id == trip_id)
            .options(selectinload(ItineraryDay.items))
            .order_by(ItineraryDay.day_number.asc())
        ).all()
    }

    item_count = 0
    for generated_day in generated_days:
        day = days_by_number.get(generated_day.day_number)
        if not day:
            day = ItineraryDay(trip_id=trip_id, day_number=generated_day.day_number)
            db.add(day)
            db.flush()
            days_by_number[generated_day.day_number] = day

        day.title = generated_day.title
        day.summary = generated_day.summary
        preserved_sort_offset = len([item for item in day.items if item.is_locked or item.is_booked])

        for index, item_data in enumerate(generated_day.items):
            place = None
            if item_data.place_name:
                place_key = normalize_place_key(item_data.place_name, None, None)
                place = places_by_key.get(place_key)
                if not place:
                    place = db.scalar(select(Place).where(func.lower(Place.name) == item_data.place_name.lower()))

            db.add(
                ItineraryItem(
                    itinerary_day_id=day.id,
                    place_id=place.id if place else None,
                    title=item_data.title,
                    description=item_data.description,
                    start_time=parse_time_string(item_data.start_time),
                    end_time=parse_time_string(item_data.end_time),
                    category=item_data.category,
                    is_locked=False,
                    is_booked=False,
                    sort_order=item_data.sort_order or preserved_sort_offset + index + 1,
                )
            )
            item_count += 1

    return (len(generated_days), item_count, preserved_count)


def persist_build_trip_output(db: Session, *, trip: Trip, output: BuildTripOutput) -> dict:
    places_by_key: dict[tuple[str, str, str], Place] = {}
    for index, place_data in enumerate(output.places):
        place = get_or_create_generated_place(db, place_data)
        link_place_to_trip(db, trip_id=trip.id, place=place, notes=place_data.reason, priority=index + 1)
        places_by_key[normalize_place_key(place.name, place.city, place.country)] = place
        places_by_key[normalize_place_key(place.name, None, None)] = place

    days_count, items_count, preserved_count = replace_generated_itinerary(
        db,
        trip_id=trip.id,
        generated_days=output.itinerary,
        places_by_key=places_by_key,
    )

    existing_task_titles = {
        title.lower()
        for title in db.scalars(select(ChecklistItem.title).where(ChecklistItem.trip_id == trip.id)).all()
    }
    checklist_count = 0
    for item in output.checklist:
        if item.title.lower() in existing_task_titles:
            continue
        due_label = item.due_label or item.description
        db.add(
            ChecklistItem(
                trip_id=trip.id,
                title=item.title,
                due_label=due_label[:120] if due_label else None,
                priority=item.priority,
                is_completed=False,
            )
        )
        checklist_count += 1

    planning_context = dict(trip.planning_context or {})
    planning_context["build_trip"] = {
        "trip_summary": output.trip_summary,
        "assumptions": output.assumptions,
        "warnings": output.warnings,
        "budget": output.budget.model_dump() if output.budget else None,
        "last_built_at": utc_now().isoformat(),
    }
    trip.planning_context = planning_context
    trip.status = "Planning"
    trip.progress = max(trip.progress, 60)

    if output.budget and output.budget.total_estimate is not None:
        try:
            trip.budget_amount = Decimal(str(output.budget.total_estimate))
        except InvalidOperation:
            pass

    return {
        "places": len(output.places),
        "days": days_count,
        "items": items_count,
        "preserved_items": preserved_count,
        "checklist_items": checklist_count,
        "budget_estimated": bool(output.budget),
    }


def create_agent_event(
    db: Session,
    *,
    run: AgentRun,
    event_type: str,
    payload: dict | None = None,
    status: str = "complete",
) -> AgentEvent:
    event = AgentEvent(
        trip_id=run.trip_id,
        agent_run_id=run.id,
        event_type=event_type,
        payload=payload or {},
        title=event_type,
        detail=json.dumps(payload or {}),
        status=status,
    )
    db.add(event)
    return event


def get_or_create_beta_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == BETA_USER_EMAIL))
    if user:
        return user

    user = User(display_name=BETA_USER_NAME, email=BETA_USER_EMAIL)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_beta_trip_or_404(db: Session, trip_id: str) -> Trip:
    user = get_or_create_beta_user(db)
    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip


def apply_patch_values(model, patch: BaseModel, fields: Iterable[str]) -> None:
    values = patch.model_dump(exclude_unset=True)
    for field in fields:
        if field in values:
            setattr(model, field, values[field])


@app.get("/health")
def health():
    return {"status": "ok", "version": "v0.4"}


@app.post("/dev/login", response_model=UserRead)
def dev_login(db: Session = Depends(get_db)):
    return get_or_create_beta_user(db)


@app.get("/dev/session", response_model=UserRead)
def dev_session(db: Session = Depends(get_db)):
    return get_or_create_beta_user(db)


@app.get("/trips", response_model=list[TripRead])
def list_trips(db: Session = Depends(get_db)):
    user = get_or_create_beta_user(db)
    return db.scalars(select(Trip).where(Trip.user_id == user.id).order_by(Trip.updated_at.desc())).all()


@app.post("/trips", response_model=TripRead)
def create_trip(body: TripCreate, db: Session = Depends(get_db)):
    user = get_or_create_beta_user(db)
    trip = Trip(user_id=user.id, **body.model_dump())
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip


@app.get("/trips/{trip_id}", response_model=TripRead)
def get_trip(trip_id: str, db: Session = Depends(get_db)):
    return get_beta_trip_or_404(db, trip_id)


@app.patch("/trips/{trip_id}", response_model=TripRead)
def patch_trip(trip_id: str, body: TripPatch, db: Session = Depends(get_db)):
    trip = get_beta_trip_or_404(db, trip_id)
    apply_patch_values(
        trip,
        body,
        ("title", "destination", "start_date", "end_date", "status", "progress", "budget_amount"),
    )
    db.commit()
    db.refresh(trip)
    return trip


@app.get("/trips/{trip_id}/messages", response_model=list[ChatMessageRead])
def list_messages(trip_id: str, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    return db.scalars(
        select(ChatMessage).where(ChatMessage.trip_id == trip_id).order_by(ChatMessage.created_at.asc())
    ).all()


@app.post("/trips/{trip_id}/messages", response_model=ChatMessageRead)
def create_message(trip_id: str, body: ChatMessageCreate, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    role = body.role.lower()
    if role not in CHAT_ROLES:
        raise HTTPException(status_code=422, detail="Message role must be user, assistant, or system")

    message = ChatMessage(trip_id=trip_id, role=role, content=body.content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@app.get("/trips/{trip_id}/itinerary", response_model=list[ItineraryDayRead])
def list_itinerary(trip_id: str, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    return db.scalars(
        select(ItineraryDay)
        .where(ItineraryDay.trip_id == trip_id)
        .options(selectinload(ItineraryDay.items))
        .order_by(ItineraryDay.day_number.asc())
    ).all()


@app.post("/trips/{trip_id}/itinerary/days", response_model=ItineraryDayRead)
def create_itinerary_day(trip_id: str, body: ItineraryDayCreate, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    day = ItineraryDay(trip_id=trip_id, **body.model_dump())
    db.add(day)
    db.commit()
    db.refresh(day)
    return day


@app.patch("/itinerary-items/{item_id}", response_model=ItineraryItemRead)
def patch_itinerary_item(item_id: str, body: ItineraryItemPatch, db: Session = Depends(get_db)):
    user = get_or_create_beta_user(db)
    item = db.scalar(
        select(ItineraryItem)
        .join(ItineraryDay)
        .join(Trip)
        .where(ItineraryItem.id == item_id, Trip.user_id == user.id)
    )
    if not item:
        raise HTTPException(status_code=404, detail="Itinerary item not found")

    apply_patch_values(
        item,
        body,
        ("title", "description", "start_time", "end_time", "category", "is_locked", "is_booked", "sort_order"),
    )
    db.commit()
    db.refresh(item)
    return item


@app.get("/trips/{trip_id}/places", response_model=list[TripPlaceRead])
def list_places(trip_id: str, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    return db.scalars(
        select(TripPlace)
        .where(TripPlace.trip_id == trip_id)
        .options(joinedload(TripPlace.place))
        .order_by(TripPlace.priority.asc().nullslast(), TripPlace.created_at.asc())
    ).unique().all()


@app.post("/trips/{trip_id}/places", response_model=TripPlaceRead)
def create_place(trip_id: str, body: PlaceCreate, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    if body.status not in TRIP_PLACE_STATUSES:
        raise HTTPException(status_code=422, detail="Place status is invalid")

    place = Place(
        name=body.name,
        category=body.category,
        city=body.city,
        country=body.country,
        latitude=body.latitude,
        longitude=body.longitude,
    )
    db.add(place)
    db.flush()

    trip_place = TripPlace(
        trip_id=trip_id,
        place_id=place.id,
        status=body.status,
        notes=body.notes,
        priority=body.priority,
    )
    db.add(trip_place)
    db.commit()
    return db.scalar(
        select(TripPlace).where(TripPlace.id == trip_place.id).options(joinedload(TripPlace.place))
    )


@app.patch("/trip-places/{trip_place_id}", response_model=TripPlaceRead)
def patch_trip_place(trip_place_id: str, body: TripPlacePatch, db: Session = Depends(get_db)):
    user = get_or_create_beta_user(db)
    trip_place = db.scalar(
        select(TripPlace)
        .join(Trip)
        .where(TripPlace.id == trip_place_id, Trip.user_id == user.id)
        .options(joinedload(TripPlace.place))
    )
    if not trip_place:
        raise HTTPException(status_code=404, detail="Trip place not found")

    values = body.model_dump(exclude_unset=True)
    if "status" in values and values["status"] not in TRIP_PLACE_STATUSES:
        raise HTTPException(status_code=422, detail="Place status is invalid")

    apply_patch_values(trip_place, body, ("status", "notes", "priority"))
    db.commit()
    return db.scalar(
        select(TripPlace).where(TripPlace.id == trip_place.id).options(joinedload(TripPlace.place))
    )


@app.get("/trips/{trip_id}/checklist", response_model=list[ChecklistItemRead])
def list_checklist(trip_id: str, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    return db.scalars(
        select(ChecklistItem)
        .where(ChecklistItem.trip_id == trip_id)
        .order_by(ChecklistItem.is_completed.asc(), ChecklistItem.created_at.asc())
    ).all()


@app.get("/trips/{trip_id}/agent-events", response_model=list[AgentEventRead])
def list_agent_events(trip_id: str, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    return db.scalars(
        select(AgentEvent)
        .where(AgentEvent.trip_id == trip_id)
        .order_by(AgentEvent.created_at.desc())
        .limit(30)
    ).all()


@app.post("/trips/{trip_id}/agent/build-trip")
def build_trip_agent(trip_id: str, db: Session = Depends(get_db)):
    user = get_or_create_beta_user(db)
    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    run = AgentRun(
        trip_id=trip.id,
        user_id=user.id,
        run_type="build_trip",
        status="running",
        input_text="Build My Trip",
    )
    db.add(run)
    db.flush()
    create_agent_event(db, run=run, event_type="agent_run.started", payload={"run_type": "build_trip"}, status="active")
    db.commit()
    log_event(
        logger,
        logging.INFO,
        "agent_run.started",
        agent_run_id=run.id,
        trip_id=trip.id,
        user_id=user.id,
        run_type="build_trip",
    )

    run_id = run.id
    trip_id_for_stream = trip.id

    def stream_events():
        stream_db = SessionLocal()

        def emit(run_record: AgentRun, event_type: str, payload: dict | None = None, status: str = "complete"):
            create_agent_event(stream_db, run=run_record, event_type=event_type, payload=payload or {}, status=status)
            stream_db.commit()
            log_event(
                logger,
                logging.INFO,
                event_type,
                agent_run_id=run_record.id,
                trip_id=run_record.trip_id,
                status=status,
                payload=payload or {},
            )
            return ndjson({"type": "agent_event", "event": event_type, "payload": payload or {}})

        try:
            run_record = stream_db.get(AgentRun, run_id)
            if not run_record:
                yield ndjson({"type": "error", "message": "Build My Trip run could not be loaded."})
                return

            yield ndjson(
                {
                    "type": "agent_event",
                    "event": "agent_run.started",
                    "payload": {"agent_run_id": run_id},
                }
            )

            if not os.getenv("OPENAI_API_KEY"):
                run_record.status = "failed"
                run_record.error_message = "Wayfinder is not configured yet."
                run_record.finished_at = utc_now()
                create_agent_event(
                    stream_db,
                    run=run_record,
                    event_type="agent_run.failed",
                    payload={"message": "Wayfinder is not configured yet."},
                    status="failed",
                )
                stream_db.commit()
                log_event(
                    logger,
                    logging.ERROR,
                    "agent_run.failed",
                    agent_run_id=run_record.id,
                    trip_id=run_record.trip_id,
                    run_type="build_trip",
                    error="Wayfinder is not configured yet.",
                )
                yield ndjson({"type": "error", "message": "Wayfinder is not configured yet."})
                return

            trip_record = stream_db.scalar(
                select(Trip)
                .where(Trip.id == trip_id_for_stream)
                .options(selectinload(Trip.messages))
            )
            if not trip_record:
                raise RuntimeError("Trip could not be loaded.")

            recent_messages = list(
                reversed(
                    stream_db.scalars(
                        select(ChatMessage)
                        .where(ChatMessage.trip_id == trip_id_for_stream)
                        .order_by(ChatMessage.created_at.desc())
                        .limit(12)
                    ).all()
                )
            )
            days = stream_db.scalars(
                select(ItineraryDay)
                .where(ItineraryDay.trip_id == trip_id_for_stream)
                .options(selectinload(ItineraryDay.items))
                .order_by(ItineraryDay.day_number.asc())
            ).all()
            places = stream_db.scalars(
                select(TripPlace)
                .where(TripPlace.trip_id == trip_id_for_stream)
                .options(joinedload(TripPlace.place))
                .order_by(TripPlace.priority.asc().nullslast(), TripPlace.created_at.asc())
            ).unique().all()

            yield emit(
                run_record,
                "trip.context_loaded",
                {
                    "messages": len(recent_messages),
                    "days": len(days),
                    "places": len(places),
                },
            )

            prompt = build_structured_trip_prompt(trip_record, recent_messages, days, places)
            yield emit(run_record, "build_trip.prompt_prepared", {"characters": len(prompt)})

            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            log_event(
                logger,
                logging.INFO,
                "build_trip.llm_request.started",
                agent_run_id=run_record.id,
                trip_id=trip_id_for_stream,
                model=os.getenv("OPENAI_MODEL", "gpt-5.2"),
            )
            response = client.responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-5.2"),
                input=[
                    {"role": "system", "content": BUILD_TRIP_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_output_tokens=5000,
            )

            response_text = get_response_text(response)
            if not response_text.strip():
                raise RuntimeError("Build My Trip returned an empty response.")

            try:
                output = BuildTripOutput.model_validate(parse_json_object(response_text))
            except (ValidationError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Build My Trip returned invalid structured output: {exc}") from exc

            yield emit(
                run_record,
                "build_trip.output_received",
                {
                    "days": len(output.itinerary),
                    "places": len(output.places),
                    "checklist_items": len(output.checklist),
                },
            )

            counts = persist_build_trip_output(stream_db, trip=trip_record, output=output)
            log_event(
                logger,
                logging.INFO,
                "build_trip.persistence.completed",
                agent_run_id=run_record.id,
                trip_id=trip_id_for_stream,
                counts=counts,
            )
            create_agent_event(stream_db, run=run_record, event_type="place.recommended", payload={"count": counts["places"]})
            create_agent_event(
                stream_db,
                run=run_record,
                event_type="itinerary.generated",
                payload={
                    "days": counts["days"],
                    "items": counts["items"],
                    "preserved_items": counts["preserved_items"],
                },
            )
            create_agent_event(
                stream_db,
                run=run_record,
                event_type="booking_checklist.generated",
                payload={"count": counts["checklist_items"]},
            )
            if counts["budget_estimated"]:
                create_agent_event(
                    stream_db,
                    run=run_record,
                    event_type="budget.estimated",
                    payload={"total_estimate": output.budget.total_estimate if output.budget else None},
                )

            assistant_message = ChatMessage(
                trip_id=trip_id_for_stream,
                role="assistant",
                content=output.trip_summary,
            )
            stream_db.add(assistant_message)
            run_record.status = "completed"
            run_record.output_summary = output.trip_summary[:500]
            run_record.finished_at = utc_now()
            create_agent_event(
                stream_db,
                run=run_record,
                event_type="agent_run.completed",
                payload=counts,
                status="complete",
            )
            stream_db.commit()
            log_event(
                logger,
                logging.INFO,
                "agent_run.completed",
                agent_run_id=run_record.id,
                trip_id=trip_id_for_stream,
                run_type="build_trip",
                counts=counts,
            )

            yield ndjson({"type": "agent_event", "event": "place.recommended", "payload": {"count": counts["places"]}})
            yield ndjson(
                {
                    "type": "agent_event",
                    "event": "itinerary.generated",
                    "payload": {
                        "days": counts["days"],
                        "items": counts["items"],
                        "preserved_items": counts["preserved_items"],
                    },
                }
            )
            yield ndjson(
                {
                    "type": "agent_event",
                    "event": "booking_checklist.generated",
                    "payload": {"count": counts["checklist_items"]},
                }
            )
            if counts["budget_estimated"]:
                yield ndjson(
                    {
                        "type": "agent_event",
                        "event": "budget.estimated",
                        "payload": {"total_estimate": output.budget.total_estimate if output.budget else None},
                    }
                )
            yield ndjson({"type": "delta", "text": output.trip_summary})
            yield ndjson({"type": "done", "agent_run_id": run_id})

        except Exception as exc:
            stream_db.rollback()
            run_record = stream_db.get(AgentRun, run_id)
            if run_record:
                run_record.status = "failed"
                run_record.error_message = str(exc)
                run_record.finished_at = utc_now()
                create_agent_event(
                    stream_db,
                    run=run_record,
                    event_type="agent_run.failed",
                    payload={"message": str(exc)[:500]},
                    status="failed",
                )
                stream_db.commit()
                log_event(
                    logger,
                    logging.ERROR,
                    "agent_run.failed",
                    agent_run_id=run_record.id,
                    trip_id=run_record.trip_id,
                    run_type="build_trip",
                    error=str(exc),
                )
            yield ndjson(
                {
                    "type": "error",
                    "message": "Wayfinder could not build this trip. Please try again.",
                }
            )
        finally:
            stream_db.close()

    return StreamingResponse(
        stream_events(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/trips/{trip_id}/chat")
def trip_chat(trip_id: str, body: TripChatRequest, db: Session = Depends(get_db)):
    text = body.message.strip()
    if not text:
        return StreamingResponse(
            iter([ndjson({"type": "error", "message": "Please enter a travel planning message."})]),
            media_type="application/x-ndjson",
        )

    user = get_or_create_beta_user(db)
    trip = db.scalar(select(Trip).where(Trip.id == trip_id, Trip.user_id == user.id))
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    extracted_context = extract_trip_context_updates(text)
    if extracted_context:
        trip.planning_context = merge_planning_context(trip.planning_context, extracted_context)

    user_message = ChatMessage(trip_id=trip.id, role="user", content=text)
    run = AgentRun(trip_id=trip.id, user_id=user.id, run_type="trip_chat", status="running", input_text=text)
    db.add_all([user_message, run])
    db.commit()
    log_event(
        logger,
        logging.INFO,
        "agent_run.started",
        agent_run_id=run.id,
        trip_id=trip.id,
        user_id=user.id,
        run_type="trip_chat",
    )

    run_id = run.id
    trip_id_for_stream = trip.id

    def stream_events():
        assistant_text = ""
        stream_db = SessionLocal()

        try:
            run_record = stream_db.get(AgentRun, run_id)
            trip_record = stream_db.scalar(
                select(Trip).where(Trip.id == trip_id_for_stream).options(selectinload(Trip.messages))
            )

            if not run_record or not trip_record:
                yield ndjson({"type": "error", "message": "Trip chat run could not be loaded."})
                return

            if not os.getenv("OPENAI_API_KEY"):
                run_record.status = "failed"
                run_record.error_message = "Wayfinder is not configured yet."
                run_record.finished_at = utc_now()
                stream_db.commit()
                yield ndjson({"type": "error", "message": "Wayfinder is not configured yet."})
                return

            recent_messages = list(
                reversed(
                    stream_db.scalars(
                        select(ChatMessage)
                        .where(ChatMessage.trip_id == trip_id_for_stream)
                        .order_by(ChatMessage.created_at.desc())
                        .limit(12)
                    ).all()
                )
            )
            days = stream_db.scalars(
                select(ItineraryDay)
                .where(ItineraryDay.trip_id == trip_id_for_stream)
                .options(selectinload(ItineraryDay.items))
                .order_by(ItineraryDay.day_number.asc())
            ).all()
            places = stream_db.scalars(
                select(TripPlace)
                .where(TripPlace.trip_id == trip_id_for_stream)
                .options(joinedload(TripPlace.place))
                .order_by(TripPlace.priority.asc().nullslast(), TripPlace.created_at.asc())
            ).unique().all()

            context_prompt = build_trip_context_prompt(trip_record, recent_messages, days, places)
            create_agent_event(stream_db, run=run_record, event_type="trip.context_loaded", payload={})
            stream_db.commit()
            yield ndjson({"type": "agent_event", "event": "trip.context_loaded", "payload": {}})

            if extracted_context.get("origin"):
                payload = {"origin": extracted_context["origin"]}
                create_agent_event(stream_db, run=run_record, event_type="trip.origin_detected", payload=payload)
                stream_db.commit()
                yield ndjson({"type": "agent_event", "event": "trip.origin_detected", "payload": payload})

            if extracted_context.get("constraints"):
                payload = {"constraints": extracted_context["constraints"]}
                create_agent_event(stream_db, run=run_record, event_type="trip.constraint_detected", payload=payload)
                stream_db.commit()
                yield ndjson({"type": "agent_event", "event": "trip.constraint_detected", "payload": payload})

            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            stream = client.responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-5.2"),
                input=[
                    {"role": "system", "content": TRIP_AWARE_SYSTEM_PROMPT},
                    {"role": "system", "content": context_prompt},
                    {"role": "user", "content": text},
                ],
                max_output_tokens=1600,
                stream=True,
            )

            for event in stream:
                event_type = getattr(event, "type", None)

                if event_type in ("response.output_text.delta", "response.refusal.delta"):
                    delta = getattr(event, "delta", "")
                    if delta:
                        assistant_text += delta
                        yield ndjson({"type": "delta", "text": delta})

                elif event_type in ("response.failed", "response.incomplete", "error"):
                    raise RuntimeError(get_event_error_message(event))

            if assistant_text.strip():
                assistant_message = ChatMessage(
                    trip_id=trip_id_for_stream,
                    role="assistant",
                    content=assistant_text,
                )
                stream_db.add(assistant_message)

            run_record.status = "completed"
            run_record.output_summary = assistant_text[:500] if assistant_text else None
            run_record.finished_at = utc_now()
            create_agent_event(stream_db, run=run_record, event_type="assistant_message.created", payload={})
            stream_db.commit()
            log_event(
                logger,
                logging.INFO,
                "agent_run.completed",
                agent_run_id=run_record.id,
                trip_id=trip_id_for_stream,
                run_type="trip_chat",
            )

            yield ndjson({"type": "agent_event", "event": "assistant_message.created", "payload": {}})
            yield ndjson({"type": "done"})

        except Exception as exc:
            run_record = stream_db.get(AgentRun, run_id)
            if run_record:
                run_record.status = "failed"
                run_record.error_message = str(exc)
                run_record.finished_at = utc_now()
                stream_db.commit()
                log_event(
                    logger,
                    logging.ERROR,
                    "agent_run.failed",
                    agent_run_id=run_record.id,
                    trip_id=trip_id_for_stream,
                    run_type="trip_chat",
                    error=str(exc),
                )
            yield ndjson(
                {
                    "type": "error",
                    "message": "Wayfinder could not complete this request. Please try again.",
                }
            )
        finally:
            stream_db.close()

    return StreamingResponse(
        stream_events(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/travel-query")
def travel_query(body: TravelQuery):
    def stream_events():
        if not body.query.strip():
            yield ndjson({"type": "error", "message": "Please enter a travel planning question."})
            return

        if not os.getenv("OPENAI_API_KEY"):
            yield ndjson({"type": "error", "message": "Wayfinder is not configured yet."})
            return

        try:
            client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            stream = client.responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-5.2"),
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": body.query},
                ],
                max_output_tokens=1600,
                stream=True,
            )

            for event in stream:
                event_type = getattr(event, "type", None)

                if event_type in ("response.output_text.delta", "response.refusal.delta"):
                    text = getattr(event, "delta", "")
                    if text:
                        yield ndjson({"type": "delta", "text": text})

                elif event_type in ("response.failed", "response.incomplete", "error"):
                    yield ndjson({"type": "error", "message": get_event_error_message(event)})
                    return

            yield ndjson({"type": "done"})

        except Exception:
            yield ndjson({
                "type": "error",
                "message": "Wayfinder could not complete this request. Please try again.",
            })

    return StreamingResponse(
        stream_events(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
