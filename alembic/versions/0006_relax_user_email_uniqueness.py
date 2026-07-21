"""relax user email uniqueness

Revision ID: 0006_relax_user_email_uniqueness
Revises: 0005_real_auth_user_identity
Create Date: 2026-07-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_relax_user_email_uniqueness"
down_revision: str | None = "0005_real_auth_user_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("users_email_key", "users", type_="unique")


def downgrade() -> None:
    op.create_unique_constraint("users_email_key", "users", ["email"])
