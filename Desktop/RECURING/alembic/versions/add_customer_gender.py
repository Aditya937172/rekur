"""Superseded customer gender migration.

Revision ID: 0002_add_customer_gender
Revises: 0001_bootstrap
Create Date: 2026-05-21 00:00:00
"""

from typing import Sequence, Union


revision: str = "0002_add_customer_gender"
down_revision: Union[str, None] = "0001_bootstrap"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The current-schema baseline already includes customers.gender.
    pass


def downgrade() -> None:
    pass
