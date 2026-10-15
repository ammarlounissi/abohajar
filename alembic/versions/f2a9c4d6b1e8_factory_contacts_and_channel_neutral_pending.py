"""factory_contacts + channel-neutral pending_media_updates

- جدول factory_contacts: جهات اتصال المصنع حسب القناة (واتساب/تيليجرام/بريد)، وتُنسخ إليه
  أرقام factories.phone_number الحالية كجهات واتساب. العمود القديم يبقى كما هو.
- pending_media_updates: إضافة channel (الصفوف الحالية = whatsapp) وإعادة تسمية media_id إلى media_ref.

خذ نسخة احتياطية من ملف القاعدة قبل التنفيذ.

Revision ID: f2a9c4d6b1e8
Revises: 7b2c4d5e6f70
"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a9c4d6b1e8"
down_revision: Union[str, Sequence[str], None] = "7b2c4d5e6f70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    contacts = op.create_table(
        "factory_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("factory_id", sa.Integer(), sa.ForeignKey("factories.id"), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("identifier", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("channel", "identifier", name="uq_factory_contacts_channel_identifier"),
    )
    op.create_index("ix_factory_contacts_factory_id", "factory_contacts", ["factory_id"])

    # نسخ الأرقام الحالية كجهات اتصال واتساب (أرقام فقط، بلا +)
    bind = op.get_bind()
    seen = set()
    rows = []
    for factory_id, phone in bind.execute(sa.text("SELECT id, phone_number FROM factories")):
        digits = re.sub(r"\D", "", phone or "")
        if digits and digits not in seen:
            seen.add(digits)
            rows.append({"factory_id": factory_id, "channel": "whatsapp", "identifier": digits})
    if rows:
        op.bulk_insert(contacts, rows)

    with op.batch_alter_table("pending_media_updates") as batch:
        batch.add_column(sa.Column("channel", sa.String(length=20), nullable=False, server_default="whatsapp"))
        batch.alter_column(
            "media_id", new_column_name="media_ref",
            existing_type=sa.String(length=128), type_=sa.String(length=255), existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("pending_media_updates") as batch:
        batch.alter_column(
            "media_ref", new_column_name="media_id",
            existing_type=sa.String(length=255), type_=sa.String(length=128), existing_nullable=False,
        )
        batch.drop_column("channel")
    op.drop_index("ix_factory_contacts_factory_id", table_name="factory_contacts")
    op.drop_table("factory_contacts")
