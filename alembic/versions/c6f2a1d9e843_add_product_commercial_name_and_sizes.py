"""Add commercial name and sizes to products.

Revision ID: c6f2a1d9e843
Revises: b7e2c9a4d1f6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6f2a1d9e843"
down_revision: Union[str, Sequence[str], None] = "b7e2c9a4d1f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("commercial_name", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("sizes", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("products", "sizes")
    op.drop_column("products", "commercial_name")
