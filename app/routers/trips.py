from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.config import CHAT_ROLES, TRIP_PLACE_STATUSES
from app.db import get_db
from app.models import AgentEvent, ChatMessage, ChecklistItem, ItineraryDay, ItineraryItem, Place, Trip, TripPlace
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
from app.services.auth import apply_patch_values, get_beta_trip_or_404, get_or_create_beta_user

router = APIRouter()


@router.post("/dev/login", response_model=UserRead)
def dev_login(db: Session = Depends(get_db)):
    return get_or_create_beta_user(db)


@router.get("/dev/session", response_model=UserRead)
def dev_session(db: Session = Depends(get_db)):
    return get_or_create_beta_user(db)


@router.get("/trips", response_model=list[TripRead])
def list_trips(db: Session = Depends(get_db)):
    user = get_or_create_beta_user(db)
    return db.scalars(select(Trip).where(Trip.user_id == user.id).order_by(Trip.updated_at.desc())).all()


@router.post("/trips", response_model=TripRead)
def create_trip(body: TripCreate, db: Session = Depends(get_db)):
    user = get_or_create_beta_user(db)
    trip = Trip(user_id=user.id, **body.model_dump())
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip


@router.get("/trips/{trip_id}", response_model=TripRead)
def get_trip(trip_id: str, db: Session = Depends(get_db)):
    return get_beta_trip_or_404(db, trip_id)


@router.patch("/trips/{trip_id}", response_model=TripRead)
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


@router.get("/trips/{trip_id}/messages", response_model=list[ChatMessageRead])
def list_messages(trip_id: str, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    return db.scalars(
        select(ChatMessage).where(ChatMessage.trip_id == trip_id).order_by(ChatMessage.created_at.asc())
    ).all()


@router.post("/trips/{trip_id}/messages", response_model=ChatMessageRead)
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


@router.get("/trips/{trip_id}/itinerary", response_model=list[ItineraryDayRead])
def list_itinerary(trip_id: str, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    return db.scalars(
        select(ItineraryDay)
        .where(ItineraryDay.trip_id == trip_id)
        .options(selectinload(ItineraryDay.items))
        .order_by(ItineraryDay.day_number.asc())
    ).all()


@router.post("/trips/{trip_id}/itinerary/days", response_model=ItineraryDayRead)
def create_itinerary_day(trip_id: str, body: ItineraryDayCreate, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    day = ItineraryDay(trip_id=trip_id, **body.model_dump())
    db.add(day)
    db.commit()
    db.refresh(day)
    return day


@router.patch("/itinerary-items/{item_id}", response_model=ItineraryItemRead)
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


@router.get("/trips/{trip_id}/places", response_model=list[TripPlaceRead])
def list_places(trip_id: str, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    return db.scalars(
        select(TripPlace)
        .where(TripPlace.trip_id == trip_id)
        .options(joinedload(TripPlace.place))
        .order_by(TripPlace.priority.asc().nullslast(), TripPlace.created_at.asc())
    ).unique().all()


@router.post("/trips/{trip_id}/places", response_model=TripPlaceRead)
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


@router.patch("/trip-places/{trip_place_id}", response_model=TripPlaceRead)
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


@router.get("/trips/{trip_id}/checklist", response_model=list[ChecklistItemRead])
def list_checklist(trip_id: str, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    return db.scalars(
        select(ChecklistItem)
        .where(ChecklistItem.trip_id == trip_id)
        .order_by(ChecklistItem.is_completed.asc(), ChecklistItem.created_at.asc())
    ).all()


@router.get("/trips/{trip_id}/agent-events", response_model=list[AgentEventRead])
def list_agent_events(trip_id: str, db: Session = Depends(get_db)):
    get_beta_trip_or_404(db, trip_id)
    return db.scalars(
        select(AgentEvent)
        .where(AgentEvent.trip_id == trip_id)
        .order_by(AgentEvent.created_at.desc())
        .limit(30)
    ).all()
