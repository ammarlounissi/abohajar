"""Add persistent factory video generation jobs.

Revision ID: 5a7c9e2d1f40
Revises: 4d8a6b2f1c90
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5a7c9e2d1f40"
down_revision: Union[str, Sequence[str], None] = "4d8a6b2f1c90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "factory_video_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("factory_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("sku", sa.String(length=50), nullable=True),
        sa.Column("template", sa.String(length=40), nullable=False),
        sa.Column("duration", sa.Float(), nullable=False),
        sa.Column("audio", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("video_url", sa.String(length=2000), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("images_used", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["factory_id"], ["factories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_factory_video_jobs_factory_id", "factory_video_jobs", ["factory_id"],
    )
    op.create_index(
        "ix_factory_video_jobs_status", "factory_video_jobs", ["status"],
    )


def downgrade() -> None:
    op.drop_index("ix_factory_video_jobs_status", table_name="factory_video_jobs")
    op.drop_index("ix_factory_video_jobs_factory_id", table_name="factory_video_jobs")
    op.drop_table("factory_video_jobs")
