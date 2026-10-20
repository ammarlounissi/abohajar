"""post_deliveries.caption: نص كل قناة كما نُشر (قالب خاص بكل منصة)

عمود نصي اختياري: NULL = يُنشر نص المنشور العام posts.description كما كان. الصفوف الموجودة تبقى NULL.
خذ نسخة احتياطية من ملف القاعدة بختم زمني قبل التنفيذ.

Revision ID: e7c2a9d4f1b3
Revises: d3a8f1c5b7e9
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7c2a9d4f1b3"
down_revision: Union[str, Sequence[str], None] = "d3a8f1c5b7e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("post_deliveries") as batch:
        batch.add_column(sa.Column("caption", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("post_deliveries") as batch:
        batch.drop_column("caption")
