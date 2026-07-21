from __future__ import annotations

import logging
import re
from secrets import token_hex

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config import AUTH_DEV_BYPASS, CHAT_ROLES, TRIP_PLACE_STATUSES
from app.db import get_db
from app.logging import get_logger, log_event
from app.models import AgentEvent, AgentRun, ChatMessage, ChecklistItem, ItineraryDay, ItineraryItem, Place, Trip, TripPlace, User, utc_now
from app.schemas import (
    AgentEventRead,
    AgentRunRead,
    ChatMessageCreate,
    ChatMessageRead,
    ChecklistItemRead,
    ItineraryDayCreate,
    ItineraryDayRead,
    ItineraryItemPatch,
    ItineraryItemRead,
    PlaceCreate,
    PublicChecklistItemRead,
    PublicItineraryDayRead,
    PublicItineraryItemRead,
    PublicTripBudgetRead,
    PublicTripPlaceRead,
    PublicTripRead,
    TripCreate,
    TripPatch,
    TripPlacePatch,
    TripPlaceRead,
    TripRead,
    TripShareStatus,
    UserRead,
)
from app.services.auth import apply_patch_values, get_current_user, get_or_create_beta_user, get_owned_trip_or_404

router = APIRouter()
logger = get_logger("wayfinder.api")


def slug_prefix(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:44].strip("-") or "trip"


def generate_share_slug(db: Session, trip: Trip) -> str:
    prefix = slug_prefix(f"{trip.title} {trip.destination}")
    for _ in range(8):
        candidate = f"{prefix}-{token_hex(3)}"
        if not db.scalar(select(Trip.id).where(Trip.share_slug == candidate)):
            return candidate
    return f"{prefix}-{token_hex(6)}"


def share_status_for_trip(trip: Trip) -> TripShareStatus:
    return TripShareStatus(
        share_enabled=trip.share_enabled,
        share_slug=trip.share_slug,
        share_path=f"/share/{trip.share_slug}" if trip.share_slug else None,
        share_created_at=trip.share_created_at,
        share_updated_at=trip.share_updated_at,
    )


def public_budget_from_context(build_trip: dict) -> PublicTripBudgetRead | None:
    budget = build_trip.get("budget")
    if not isinstance(budget, dict):
        return None
    return PublicTripBudgetRead(
        currency=budget.get("currency") or "USD",
        total_estimate=budget.get("total_estimate"),
        notes=budget.get("notes") or [],
        categories=budget.get("categories") or [],
    )


def public_trip_response(trip: Trip) -> PublicTripRead:
    build_trip = (trip.planning_context or {}).get("build_trip") or {}
    public_places = sorted(
        trip.trip_places,
        key=lambda trip_place: (
            trip_place.priority is None,
            trip_place.priority or 0,
            trip_place.place.name if trip_place.place else "",
        ),
    )
    public_checklist = sorted(trip.checklist_items, key=lambda item: (item.is_completed, item.created_at))
    return PublicTripRead(
        title=trip.title,
        destination=trip.destination,
        start_date=trip.start_date,
        end_date=trip.end_date,
        status=trip.status,
        progress=trip.progress,
        summary=build_trip.get("trip_summary"),
        budget_amount=trip.budget_amount,
        budget=public_budget_from_context(build_trip),
        itinerary_days=[
            PublicItineraryDayRead(
                day_number=day.day_number,
                date=day.date,
                title=day.title,
                summary=day.summary,
                items=[
                    PublicItineraryItemRead(
                        title=item.title,
                        description=item.description,
                        start_time=item.start_time,
                        end_time=item.end_time,
                        category=item.category,
                        is_booked=item.is_booked,
                    )
                    for item in day.items
                ],
            )
            for day in trip.itinerary_days
        ],
        places=[
            PublicTripPlaceRead(
                name=trip_place.place.name,
                category=trip_place.place.category,
                city=trip_place.place.city,
                country=trip_place.place.country,
                status=trip_place.status,
                notes=trip_place.notes,
                priority=trip_place.priority,
            )
            for trip_place in public_places
            if trip_place.place
        ],
        checklist_items=[
            PublicChecklistItemRead(
                title=item.title,
                due_label=item.due_label,
                priority=item.priority,
                is_completed=item.is_completed,
            )
            for item in public_checklist
        ],
        assumptions=build_trip.get("assumptions") or [],
        warnings=build_trip.get("warnings") or [],
        generated_at=build_trip.get("last_built_at"),
        updated_at=trip.updated_at,
    )


@router.post("/dev/login", response_model=UserRead)
def dev_login(db: Session = Depends(get_db)):
    if not AUTH_DEV_BYPASS:
        raise HTTPException(status_code=404, detail="Not found")
    return get_or_create_beta_user(db)


@router.get("/dev/session", response_model=UserRead)
def dev_session(db: Session = Depends(get_db)):
    if not AUTH_DEV_BYPASS:
        raise HTTPException(status_code=404, detail="Not found")
    return get_or_create_beta_user(db)


@router.get("/trips", response_model=list[TripRead])
def list_trips(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return db.scalars(select(Trip).where(Trip.user_id == user.id).order_by(Trip.updated_at.desc())).all()


@router.post("/trips", response_model=TripRead)
def create_trip(body: TripCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = Trip(user_id=user.id, **body.model_dump())
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip


@router.get("/trips/{trip_id}", response_model=TripRead)
def get_trip(trip_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return get_owned_trip_or_404(db, trip_id, user)


@router.get("/trips/{trip_id}/share", response_model=TripShareStatus)
def get_trip_share_status(trip_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = get_owned_trip_or_404(db, trip_id, user)
    return share_status_for_trip(trip)


@router.post("/trips/{trip_id}/share", response_model=TripShareStatus)
def enable_trip_share(trip_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = get_owned_trip_or_404(db, trip_id, user)
    now = utc_now()

    if not trip.share_slug:
        trip.share_slug = generate_share_slug(db, trip)
    if not trip.share_created_at:
        trip.share_created_at = now
    trip.share_enabled = True
    trip.share_updated_at = now

    db.commit()
    db.refresh(trip)
    log_event(logger, logging.INFO, "share.enabled", trip_id=trip.id, share_slug=trip.share_slug)
    return share_status_for_trip(trip)


@router.delete("/trips/{trip_id}/share", response_model=TripShareStatus)
def disable_trip_share(trip_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = get_owned_trip_or_404(db, trip_id, user)
    trip.share_enabled = False
    trip.share_updated_at = utc_now()

    db.commit()
    db.refresh(trip)
    log_event(logger, logging.INFO, "share.disabled", trip_id=trip.id, share_slug=trip.share_slug)
    return share_status_for_trip(trip)


@router.get("/public/trips/{share_slug}", response_model=PublicTripRead)
def get_public_trip(share_slug: str, db: Session = Depends(get_db)):
    trip = db.scalar(
        select(Trip)
        .where(Trip.share_slug == share_slug, Trip.share_enabled.is_(True))
        .options(
            selectinload(Trip.itinerary_days).selectinload(ItineraryDay.items),
            selectinload(Trip.trip_places).joinedload(TripPlace.place),
            selectinload(Trip.checklist_items),
        )
    )
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")

    log_event(logger, logging.INFO, "share.viewed", share_slug=share_slug)
    return public_trip_response(trip)


@router.patch("/trips/{trip_id}", response_model=TripRead)
def patch_trip(trip_id: str, body: TripPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    trip = get_owned_trip_or_404(db, trip_id, user)
    apply_patch_values(
        trip,
        body,
        ("title", "destination", "start_date", "end_date", "status", "progress", "budget_amount"),
    )
    db.commit()
    db.refresh(trip)
    return trip


@router.get("/trips/{trip_id}/messages", response_model=list[ChatMessageRead])
def list_messages(trip_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_trip_or_404(db, trip_id, user)
    return db.scalars(
        select(ChatMessage).where(ChatMessage.trip_id == trip_id).order_by(ChatMessage.created_at.asc())
    ).all()


@router.post("/trips/{trip_id}/messages", response_model=ChatMessageRead)
def create_message(trip_id: str, body: ChatMessageCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_trip_or_404(db, trip_id, user)
    role = body.role.lower()
    if role not in CHAT_ROLES:
        raise HTTPException(status_code=422, detail="Message role must be user, assistant, or system")

    message = ChatMessage(trip_id=trip_id, role=role, content=body.content)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


@router.get("/trips/{trip_id}/itinerary", response_model=list[ItineraryDayRead])
def list_itinerary(trip_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_trip_or_404(db, trip_id, user)
    return db.scalars(
        select(ItineraryDay)
        .where(ItineraryDay.trip_id == trip_id)
        .options(selectinload(ItineraryDay.items))
        .order_by(ItineraryDay.day_number.asc())
    ).all()


@router.post("/trips/{trip_id}/itinerary/days", response_model=ItineraryDayRead)
def create_itinerary_day(trip_id: str, body: ItineraryDayCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_trip_or_404(db, trip_id, user)
    day = ItineraryDay(trip_id=trip_id, **body.model_dump())
    db.add(day)
    db.commit()
    db.refresh(day)
    return day


@router.patch("/itinerary-items/{item_id}", response_model=ItineraryItemRead)
def patch_itinerary_item(item_id: str, body: ItineraryItemPatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
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


@router.get("/trips/{trip_id}/places", response_model=list[TripPlaceRead])
def list_places(trip_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_trip_or_404(db, trip_id, user)
    return db.scalars(
        select(TripPlace)
        .where(TripPlace.trip_id == trip_id)
        .options(joinedload(TripPlace.place))
        .order_by(TripPlace.priority.asc().nullslast(), TripPlace.created_at.asc())
    ).unique().all()


@router.post("/trips/{trip_id}/places", response_model=TripPlaceRead)
def create_place(trip_id: str, body: PlaceCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_trip_or_404(db, trip_id, user)
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


@router.patch("/trip-places/{trip_place_id}", response_model=TripPlaceRead)
def patch_trip_place(trip_place_id: str, body: TripPlacePatch, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
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


@router.get("/trips/{trip_id}/checklist", response_model=list[ChecklistItemRead])
def list_checklist(trip_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_trip_or_404(db, trip_id, user)
    return db.scalars(
        select(ChecklistItem)
        .where(ChecklistItem.trip_id == trip_id)
        .order_by(ChecklistItem.is_completed.asc(), ChecklistItem.created_at.asc())
    ).all()


@router.get("/trips/{trip_id}/agent-events", response_model=list[AgentEventRead])
def list_agent_events(trip_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    get_owned_trip_or_404(db, trip_id, user)
    return db.scalars(
        select(AgentEvent)
        .where(AgentEvent.trip_id == trip_id)
        .order_by(AgentEvent.created_at.desc())
        .limit(30)
    ).all()


@router.get("/trips/{trip_id}/agent-runs/{agent_run_id}", response_model=AgentRunRead)
def get_agent_run(
    trip_id: str,
    agent_run_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    run = db.scalar(
        select(AgentRun)
        .where(AgentRun.id == agent_run_id, AgentRun.trip_id == trip_id, AgentRun.user_id == user.id)
        .options(selectinload(AgentRun.events))
    )
    if not run:
        raise HTTPException(status_code=404, detail="Agent run not found")
    return run
