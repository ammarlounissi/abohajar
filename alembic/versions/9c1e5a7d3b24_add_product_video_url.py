"""add product video_url

Revision ID: 9c1e5a7d3b24
Revises: 2f4c7a9d1b30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9c1e5a7d3b24"
down_revision: Union[str, Sequence[str], None] = "2f4c7a9d1b30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The merged factory-bot migration already adds this column on fresh installs.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("products")}
    if "video_url" not in columns:
        op.add_column("products", sa.Column("video_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    # The column is owned by the earlier factory-bot migration.
    pass
