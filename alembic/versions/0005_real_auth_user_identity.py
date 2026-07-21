"""real auth user identity

Revision ID: 0005_real_auth_user_identity
Revises: 0004_shareable_trip_page
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_real_auth_user_identity"
down_revision: str | None = "0004_shareable_trip_page"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("auth_provider", sa.String(length=40), nullable=True))
    op.add_column("users", sa.Column("auth_provider_user_id", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.String(length=500), nullable=True))
    op.add_column("users", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))

    op.execute(
        """
        UPDATE users
        SET
            auth_provider = 'dev',
            auth_provider_user_id = CASE
                WHEN email = 'beta@wayfinder.test' THEN 'shared-beta'
                ELSE id
            END,
            updated_at = created_at
        """
    )

    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=True)
    op.alter_column("users", "auth_provider", existing_type=sa.String(length=40), nullable=False)
    op.alter_column("users", "auth_provider_user_id", existing_type=sa.String(length=255), nullable=False)
    op.alter_column("users", "updated_at", existing_type=sa.DateTime(timezone=True), nullable=False)
    op.create_index(op.f("ix_users_auth_provider_user_id"), "users", ["auth_provider_user_id"], unique=False)
    op.create_unique_constraint(
        "uq_users_auth_provider_user_id",
        "users",
        ["auth_provider", "auth_provider_user_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_users_auth_provider_user_id", "users", type_="unique")
    op.drop_index(op.f("ix_users_auth_provider_user_id"), table_name="users")
    op.alter_column("users", "email", existing_type=sa.String(length=255), nullable=False)
    op.drop_column("users", "updated_at")
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "auth_provider_user_id")
    op.drop_column("users", "auth_provider")
