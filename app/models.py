from __future__ import annotations

from datetime import date as DateType
from datetime import datetime, timezone
from datetime import time as TimeType
from uuid import uuid4

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def uuid_pk() -> str:
    return str(uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    trips: Mapped[list["Trip"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Trip(Base):
    __tablename__ = "trips"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    destination: Mapped[str] = mapped_column(String(180), nullable=False)
    start_date: Mapped[DateType | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[DateType | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="Draft", nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    budget_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="trips")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
    )
    trip_places: Mapped[list["TripPlace"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
    )
    itinerary_days: Mapped[list["ItineraryDay"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
        order_by="ItineraryDay.day_number",
    )
    checklist_items: Mapped[list["ChecklistItem"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
    )
    agent_events: Mapped[list["AgentEvent"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
        order_by="AgentEvent.created_at.desc()",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    trip: Mapped[Trip] = relationship(back_populates="messages")


class Place(Base):
    __tablename__ = "places"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    name: Mapped[str] = mapped_column(String(180), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    trip_places: Mapped[list["TripPlace"]] = relationship(back_populates="place")
    itinerary_items: Mapped[list["ItineraryItem"]] = relationship(back_populates="place")


class TripPlace(Base):
    __tablename__ = "trip_places"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True, nullable=False)
    place_id: Mapped[str] = mapped_column(ForeignKey("places.id", ondelete="CASCADE"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="suggested", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    trip: Mapped[Trip] = relationship(back_populates="trip_places")
    place: Mapped[Place] = relationship(back_populates="trip_places")


class ItineraryDay(Base):
    __tablename__ = "itinerary_days"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True, nullable=False)
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[DateType | None] = mapped_column(Date, nullable=True)
    title: Mapped[str | None] = mapped_column(String(180), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    trip: Mapped[Trip] = relationship(back_populates="itinerary_days")
    items: Mapped[list["ItineraryItem"]] = relationship(
        back_populates="itinerary_day",
        cascade="all, delete-orphan",
        order_by="ItineraryItem.sort_order",
    )


class ItineraryItem(Base):
    __tablename__ = "itinerary_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    itinerary_day_id: Mapped[str] = mapped_column(
        ForeignKey("itinerary_days.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    place_id: Mapped[str | None] = mapped_column(ForeignKey("places.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_time: Mapped[TimeType | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[TimeType | None] = mapped_column(Time, nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_booked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    itinerary_day: Mapped[ItineraryDay] = relationship(back_populates="items")
    place: Mapped[Place | None] = relationship(back_populates="itinerary_items")


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(220), nullable=False)
    due_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    priority: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_completed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    trip: Mapped[Trip] = relationship(back_populates="checklist_items")


class AgentEvent(Base):
    __tablename__ = "agent_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_pk)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(180), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    trip: Mapped[Trip] = relationship(back_populates="agent_events")
