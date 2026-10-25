"""add factory description jobs

Revision ID: 9e7b3c2a1d64
Revises: 5a7c9e2d1f40
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9e7b3c2a1d64"
down_revision: Union[str, Sequence[str], None] = "5a7c9e2d1f40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "factory_description_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("factory_id", sa.Integer(), sa.ForeignKey("factories.id"), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_factory_description_jobs_factory_id",
        "factory_description_jobs",
        ["factory_id"],
    )
    op.create_index(
        "ix_factory_description_jobs_status",
        "factory_description_jobs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_factory_description_jobs_status", table_name="factory_description_jobs")
    op.drop_index("ix_factory_description_jobs_factory_id", table_name="factory_description_jobs")
    op.drop_table("factory_description_jobs")
