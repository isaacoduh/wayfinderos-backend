"""durable trip state

Revision ID: 0001_durable_trip_state
Revises:
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_durable_trip_state"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=False)

    op.create_table(
        "places",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("country", sa.String(length=120), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_places_name"), "places", ["name"], unique=False)

    op.create_table(
        "trips",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("destination", sa.String(length=180), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("budget_amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_trips_user_id"), "trips", ["user_id"], unique=False)

    op.create_table(
        "agent_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trip_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=180), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_events_trip_id"), "agent_events", ["trip_id"], unique=False)

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trip_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_chat_messages_trip_id"), "chat_messages", ["trip_id"], unique=False)

    op.create_table(
        "checklist_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trip_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=220), nullable=False),
        sa.Column("due_label", sa.String(length=120), nullable=True),
        sa.Column("priority", sa.String(length=40), nullable=True),
        sa.Column("is_completed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_checklist_items_trip_id"), "checklist_items", ["trip_id"], unique=False)

    op.create_table(
        "itinerary_days",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trip_id", sa.String(length=36), nullable=False),
        sa.Column("day_number", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("title", sa.String(length=180), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_itinerary_days_trip_id"), "itinerary_days", ["trip_id"], unique=False)

    op.create_table(
        "trip_places",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trip_id", sa.String(length=36), nullable=False),
        sa.Column("place_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_trip_places_place_id"), "trip_places", ["place_id"], unique=False)
    op.create_index(op.f("ix_trip_places_trip_id"), "trip_places", ["trip_id"], unique=False)

    op.create_table(
        "itinerary_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("itinerary_day_id", sa.String(length=36), nullable=False),
        sa.Column("place_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=220), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.Column("category", sa.String(length=80), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False),
        sa.Column("is_booked", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["itinerary_day_id"], ["itinerary_days.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["place_id"], ["places.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_itinerary_items_itinerary_day_id"), "itinerary_items", ["itinerary_day_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_itinerary_items_itinerary_day_id"), table_name="itinerary_items")
    op.drop_table("itinerary_items")
    op.drop_index(op.f("ix_trip_places_trip_id"), table_name="trip_places")
    op.drop_index(op.f("ix_trip_places_place_id"), table_name="trip_places")
    op.drop_table("trip_places")
    op.drop_index(op.f("ix_itinerary_days_trip_id"), table_name="itinerary_days")
    op.drop_table("itinerary_days")
    op.drop_index(op.f("ix_checklist_items_trip_id"), table_name="checklist_items")
    op.drop_table("checklist_items")
    op.drop_index(op.f("ix_chat_messages_trip_id"), table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index(op.f("ix_agent_events_trip_id"), table_name="agent_events")
    op.drop_table("agent_events")
    op.drop_index(op.f("ix_trips_user_id"), table_name="trips")
    op.drop_table("trips")
    op.drop_index(op.f("ix_places_name"), table_name="places")
    op.drop_table("places")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
