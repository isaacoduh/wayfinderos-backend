import json
import os
from collections.abc import Iterable

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import OpenAI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.db import get_db
from app.models import ChatMessage, ItineraryDay, ItineraryItem, Place, Trip, TripPlace, User
from app.schemas import (
    ChatMessageCreate,
    ChatMessageRead,
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

app = FastAPI(title="Wayfinder OS")

frontend_origin = os.getenv("FRONTEND_ORIGIN", "*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_origin] if frontend_origin != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BETA_USER_EMAIL = os.getenv("BETA_USER_EMAIL", "beta@wayfinder.test")
BETA_USER_NAME = os.getenv("BETA_USER_NAME", "Beta Tester")

CHAT_ROLES = {"user", "assistant", "system"}
TRIP_PLACE_STATUSES = {"suggested", "interested", "booked", "skipped"}


class TravelQuery(BaseModel):
    query: str


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
    return {"status": "ok", "version": "v0.2"}


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
