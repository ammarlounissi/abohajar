"""add factory reel requests

Revision ID: a1c5e9b3d7f2
Revises: 9e7b3c2a1d64
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c5e9b3d7f2"
down_revision: Union[str, Sequence[str], None] = "9e7b3c2a1d64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "factory_reel_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("factory_id", sa.Integer(), sa.ForeignKey("factories.id"), nullable=False),
        sa.Column("video_job_id", sa.Integer(), sa.ForeignKey("factory_video_jobs.id"), nullable=False),
        sa.Column("chat_job_id", sa.Integer(), sa.ForeignKey("factory_description_jobs.id"), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_factory_reel_requests_factory_id", "factory_reel_requests", ["factory_id"])
    op.create_index("ix_factory_reel_requests_status", "factory_reel_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_factory_reel_requests_status", table_name="factory_reel_requests")
    op.drop_index("ix_factory_reel_requests_factory_id", table_name="factory_reel_requests")
    op.drop_table("factory_reel_requests")
