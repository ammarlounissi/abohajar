"""add factory media library

Revision ID: 2f4c7a9d1b30
Revises: 8a4c1e7b2d90
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "2f4c7a9d1b30"
down_revision: Union[str, Sequence[str], None] = "8a4c1e7b2d90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=10), nullable=False),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("factory_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["factory_id"], ["factories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_factory_id", "media", ["factory_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_media_factory_id", table_name="media")
    op.drop_table("media")
