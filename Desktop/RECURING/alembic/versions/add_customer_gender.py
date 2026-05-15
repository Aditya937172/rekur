"""
Add gender column to customers table
"""

from alembic import op
import sqlalchemy as sa


def upgrade():
    op.add_column("customers", sa.Column("gender", sa.String(32), nullable=True))


def downgrade():
    op.drop_column("customers", "gender")
