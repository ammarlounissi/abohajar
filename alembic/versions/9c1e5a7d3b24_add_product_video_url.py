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


def _has_column() -> bool:
    columns = sa.inspect(op.get_bind()).get_columns("products")
    return any(c["name"] == "video_url" for c in columns)


def upgrade() -> None:
    # آمن إن كان العمود موجوداً مسبقاً (أُنشئ يدوياً أو بتشغيل سابق)
    if not _has_column():
        op.add_column("products", sa.Column("video_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    if _has_column():
        op.drop_column("products", "video_url")