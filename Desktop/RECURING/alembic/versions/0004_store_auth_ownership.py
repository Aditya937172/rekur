"""store auth ownership

Revision ID: 0004_store_auth
Revises: 0003_current_schema
Create Date: 2026-05-21 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_store_auth"
down_revision: Union[str, None] = "0003_current_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing_tables: set[str] | None = None
    try:
        existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    except Exception:
        # Offline SQL generation uses Alembic's mock connection, which cannot be
        # inspected. In that mode we emit the full DDL.
        existing_tables = None
    if existing_tables is None or "app_users" not in existing_tables:
        op.create_table(
            "app_users",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=True),
            sa.Column("external_id", sa.String(length=255), nullable=True),
            sa.Column("auth_provider", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_app_users_id"), "app_users", ["id"], unique=False)
        op.create_index("ix_app_users_email", "app_users", ["email"], unique=True)
        op.create_index(
            "ix_app_users_external_id",
            "app_users",
            ["external_id"],
            unique=False,
        )

    if existing_tables is None or "store_ownerships" not in existing_tables:
        op.create_table(
            "store_ownerships",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("store_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["store_id"], ["stores.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["app_users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "user_id",
                "store_id",
                name="uq_store_ownership_user_store",
            ),
        )
        op.create_index(
            op.f("ix_store_ownerships_id"),
            "store_ownerships",
            ["id"],
            unique=False,
        )
        op.create_index(
            "ix_store_ownerships_store_id",
            "store_ownerships",
            ["store_id"],
            unique=False,
        )
        op.create_index(
            "ix_store_ownerships_user_id",
            "store_ownerships",
            ["user_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_index("ix_store_ownerships_user_id", table_name="store_ownerships")
    op.drop_index("ix_store_ownerships_store_id", table_name="store_ownerships")
    op.drop_index(op.f("ix_store_ownerships_id"), table_name="store_ownerships")
    op.drop_table("store_ownerships")
    op.drop_index("ix_app_users_external_id", table_name="app_users")
    op.drop_index("ix_app_users_email", table_name="app_users")
    op.drop_index(op.f("ix_app_users_id"), table_name="app_users")
    op.drop_table("app_users")
