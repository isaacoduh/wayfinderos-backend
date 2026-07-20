"""async workflow reliability

Revision ID: 0003_async_workflow_reliability
Revises: 0002_trip_aware_agent_foundation
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_async_workflow_reliability"
down_revision: str | None = "0002_trip_aware_agent_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("job_id", sa.String(length=120), nullable=True))
    op.add_column("agent_runs", sa.Column("idempotency_key", sa.String(length=160), nullable=True))
    op.add_column("agent_runs", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_agent_runs_job_id"), "agent_runs", ["job_id"], unique=False)
    op.create_index(
        "ix_agent_runs_trip_run_idempotency",
        "agent_runs",
        ["trip_id", "run_type", "idempotency_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_trip_run_idempotency", table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_job_id"), table_name="agent_runs")
    op.drop_column("agent_runs", "queued_at")
    op.drop_column("agent_runs", "idempotency_key")
    op.drop_column("agent_runs", "job_id")
