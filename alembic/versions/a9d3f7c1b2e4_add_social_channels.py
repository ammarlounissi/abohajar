"""جدول social_channels: قنوات النشر (فيسبوك/إنستغرام/تيليجرام) بدل المعرّفات في الإعدادات

الجدول جديد بالكامل ولا يمس غيره. القيد الفريد على (platform, remote_id) يمنع تسجيل القناة مرتين.
خذ نسخة احتياطية من ملف القاعدة بختم زمني قبل التنفيذ.

Revision ID: a9d3f7c1b2e4
Revises: b5d8e1f3a7c2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9d3f7c1b2e4"
down_revision: Union[str, Sequence[str], None] = "b5d8e1f3a7c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "social_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("remote_id", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("platform", "remote_id", name="uq_social_channels_platform_remote_id"),
    )
    op.create_index("ix_social_channels_id", "social_channels", ["id"])
    op.create_index("ix_social_channels_platform", "social_channels", ["platform"])


def downgrade() -> None:
    op.drop_index("ix_social_channels_platform", table_name="social_channels")
    op.drop_index("ix_social_channels_id", table_name="social_channels")
    op.drop_table("social_channels")
