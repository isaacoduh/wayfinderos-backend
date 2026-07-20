"""shareable trip page

Revision ID: 0004_shareable_trip_page
Revises: 0003_async_workflow_reliability
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_shareable_trip_page"
down_revision: str | None = "0003_async_workflow_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("trips", sa.Column("share_slug", sa.String(length=220), nullable=True))
    op.add_column("trips", sa.Column("share_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("trips", sa.Column("share_created_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("trips", sa.Column("share_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_trips_share_slug"), "trips", ["share_slug"], unique=True)
    op.alter_column("trips", "share_enabled", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_trips_share_slug"), table_name="trips")
    op.drop_column("trips", "share_updated_at")
    op.drop_column("trips", "share_created_at")
    op.drop_column("trips", "share_enabled")
    op.drop_column("trips", "share_slug")
