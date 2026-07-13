from __future__ import annotations

from datetime import date as DateType
from datetime import datetime
from datetime import time as TimeType
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    display_name: str
    email: str
    created_at: datetime


class TripBase(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    destination: str = Field(min_length=1, max_length=180)
    start_date: DateType | None = None
    end_date: DateType | None = None
    status: str = "Draft"
    progress: int = Field(default=0, ge=0, le=100)
    budget_amount: Decimal | None = None


class TripCreate(TripBase):
    pass


class TripPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    destination: str | None = Field(default=None, min_length=1, max_length=180)
    start_date: DateType | None = None
    end_date: DateType | None = None
    status: str | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    budget_amount: Decimal | None = None


class TripRead(TripBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    planning_context: dict | None = None
    created_at: datetime
    updated_at: datetime


class ChatMessageCreate(BaseModel):
    role: str
    content: str = Field(min_length=1)


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trip_id: str
    role: str
    content: str
    created_at: datetime


class PlaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    category: str | None = None
    city: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    status: str = "suggested"
    notes: str | None = None
    priority: int | None = None


class PlaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    category: str | None
    city: str | None
    country: str | None
    latitude: float | None
    longitude: float | None
    created_at: datetime


class TripPlaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trip_id: str
    place_id: str
    status: str
    notes: str | None
    priority: int | None
    created_at: datetime
    updated_at: datetime
    place: PlaceRead


class TripPlacePatch(BaseModel):
    status: str | None = None
    notes: str | None = None
    priority: int | None = None


class ItineraryItemPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=220)
    description: str | None = None
    start_time: TimeType | None = None
    end_time: TimeType | None = None
    category: str | None = None
    is_locked: bool | None = None
    is_booked: bool | None = None
    sort_order: int | None = None


class ItineraryItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    itinerary_day_id: str
    place_id: str | None
    title: str
    description: str | None
    start_time: TimeType | None
    end_time: TimeType | None
    category: str | None
    is_locked: bool
    is_booked: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class ItineraryDayCreate(BaseModel):
    day_number: int
    date: DateType | None = None
    title: str | None = None
    summary: str | None = None


class ItineraryDayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trip_id: str
    day_number: int
    date: DateType | None
    title: str | None
    summary: str | None
    items: list[ItineraryItemRead] = []


class ChecklistItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trip_id: str
    title: str
    due_label: str | None
    priority: str | None
    is_completed: bool
    created_at: datetime
    updated_at: datetime


class AgentEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trip_id: str
    title: str
    detail: str | None
    status: str
    created_at: datetime
