"""product inventory metadata

Revision ID: 0005_product_inventory
Revises: 0004_store_auth
Create Date: 2026-05-29 00:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_product_inventory"
down_revision: Union[str, None] = "0004_store_auth"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = set()
    try:
        columns = {
            column["name"]
            for column in sa.inspect(op.get_bind()).get_columns("products")
        }
    except Exception:
        columns = set()

    if "in_stock" not in columns:
        op.add_column(
            "products",
            sa.Column("in_stock", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if "variant_inventory_json" not in columns:
        op.add_column("products", sa.Column("variant_inventory_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "variant_inventory_json")
    op.drop_column("products", "in_stock")
