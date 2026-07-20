from __future__ import annotations

from pydantic import BaseModel, Field


class TravelQuery(BaseModel):
    query: str


class TripChatRequest(BaseModel):
    message: str = Field(min_length=1)


class RegenerateDayRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)


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


class RegenerateDayOutput(BaseModel):
    day_title: str | None = Field(default=None, max_length=180)
    day_summary: str | None = None
    change_summary: str = Field(min_length=1, max_length=1200)
    warnings: list[str] = []
    items: list[BuildTripItineraryItem] = []
    places: list[BuildTripPlace] = []
