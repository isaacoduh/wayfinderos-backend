"""trip aware agent foundation

Revision ID: 0002_trip_aware_agent_foundation
Revises: 0001_durable_trip_state
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_trip_aware_agent_foundation"
down_revision: str | None = "0001_durable_trip_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("planning_context", sa.JSON(), nullable=True))

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("trip_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("run_type", sa.String(length=60), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("output_summary", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agent_runs_trip_id"), "agent_runs", ["trip_id"], unique=False)
    op.create_index(op.f("ix_agent_runs_user_id"), "agent_runs", ["user_id"], unique=False)

    op.add_column("agent_events", sa.Column("agent_run_id", sa.String(length=36), nullable=True))
    op.add_column("agent_events", sa.Column("event_type", sa.String(length=120), nullable=True))
    op.add_column("agent_events", sa.Column("payload", sa.JSON(), nullable=True))
    op.create_index(op.f("ix_agent_events_agent_run_id"), "agent_events", ["agent_run_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_agent_events_agent_run_id_agent_runs"),
        "agent_events",
        "agent_runs",
        ["agent_run_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_agent_events_agent_run_id_agent_runs"), "agent_events", type_="foreignkey")
    op.drop_index(op.f("ix_agent_events_agent_run_id"), table_name="agent_events")
    op.drop_column("agent_events", "payload")
    op.drop_column("agent_events", "event_type")
    op.drop_column("agent_events", "agent_run_id")
    op.drop_index(op.f("ix_agent_runs_user_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_trip_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_column("trips", "planning_context")
