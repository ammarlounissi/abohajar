"""add factory activity

Revision ID: 8a4c1e7b2d90
Revises: 3d9f6a2b1c84
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8a4c1e7b2d90"
down_revision: Union[str, Sequence[str], None] = "3d9f6a2b1c84"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "factories",
        sa.Column("activity", sa.Text(), server_default="", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("factories", "activity")
