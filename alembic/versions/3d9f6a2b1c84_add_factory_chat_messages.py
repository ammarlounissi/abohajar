"""add factory chat messages

Revision ID: 3d9f6a2b1c84
Revises: e7c2a9d4f1b3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3d9f6a2b1c84"
down_revision: Union[str, Sequence[str], None] = "e7c2a9d4f1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "factory_chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("factory_id", sa.Integer(), sa.ForeignKey("factories.id"), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("response", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_factory_chat_messages_factory_id",
        "factory_chat_messages",
        ["factory_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_factory_chat_messages_factory_id", table_name="factory_chat_messages")
    op.drop_table("factory_chat_messages")
