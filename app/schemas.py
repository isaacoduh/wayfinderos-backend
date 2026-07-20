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
    share_slug: str | None = None
    share_enabled: bool = False
    share_created_at: datetime | None = None
    share_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class TripShareStatus(BaseModel):
    share_enabled: bool
    share_slug: str | None = None
    share_path: str | None = None
    share_created_at: datetime | None = None
    share_updated_at: datetime | None = None


class PublicTripPlaceRead(BaseModel):
    name: str
    category: str | None = None
    city: str | None = None
    country: str | None = None
    status: str
    notes: str | None = None
    priority: int | None = None


class PublicItineraryItemRead(BaseModel):
    title: str
    description: str | None = None
    start_time: TimeType | None = None
    end_time: TimeType | None = None
    category: str | None = None
    is_booked: bool = False


class PublicItineraryDayRead(BaseModel):
    day_number: int
    date: DateType | None = None
    title: str | None = None
    summary: str | None = None
    items: list[PublicItineraryItemRead] = []


class PublicChecklistItemRead(BaseModel):
    title: str
    due_label: str | None = None
    priority: str | None = None
    is_completed: bool = False


class PublicTripBudgetRead(BaseModel):
    currency: str = "USD"
    total_estimate: Decimal | None = None
    notes: list[str] = []
    categories: list[dict] = []


class PublicTripRead(BaseModel):
    title: str
    destination: str
    start_date: DateType | None = None
    end_date: DateType | None = None
    status: str
    progress: int
    summary: str | None = None
    budget_amount: Decimal | None = None
    budget: PublicTripBudgetRead | None = None
    itinerary_days: list[PublicItineraryDayRead] = []
    places: list[PublicTripPlaceRead] = []
    checklist_items: list[PublicChecklistItemRead] = []
    assumptions: list[str] = []
    warnings: list[str] = []
    generated_at: datetime | None = None
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
    agent_run_id: str | None
    event_type: str | None
    payload: dict | None
    title: str
    detail: str | None
    status: str
    created_at: datetime


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trip_id: str
    user_id: str
    run_type: str
    status: str
    job_id: str | None
    input_text: str
    output_summary: str | None
    error_message: str | None
    queued_at: datetime | None
    started_at: datetime
    finished_at: datetime | None
    events: list[AgentEventRead] = []
