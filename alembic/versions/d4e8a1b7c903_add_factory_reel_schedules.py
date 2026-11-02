"""add recurring daily factory reel schedules

Revision ID: d4e8a1b7c903
Revises: c3a9f2e7b641
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8a1b7c903"
down_revision: Union[str, Sequence[str], None] = "c3a9f2e7b641"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "factory_reel_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("factory_id", sa.Integer(), sa.ForeignKey("factories.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False, server_default="gemini-3.1-flash-lite"),
        sa.Column("daily_time", sa.String(length=5), nullable=False, server_default="01:00"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Africa/Algiers"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("stop_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_on", sa.Date(), nullable=True),
        sa.Column("last_run_at", sa.DateTime(), nullable=True),
        sa.Column("last_status", sa.String(length=20), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "last_reel_request_id", sa.Integer(),
            sa.ForeignKey("factory_reel_requests.id", ondelete="SET NULL"), nullable=True,
        ),
    )
    op.create_index("ix_factory_reel_schedules_factory_id", "factory_reel_schedules", ["factory_id"])
    op.create_index("ix_factory_reel_schedules_is_active", "factory_reel_schedules", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_factory_reel_schedules_is_active", table_name="factory_reel_schedules")
    op.drop_index("ix_factory_reel_schedules_factory_id", table_name="factory_reel_schedules")
    op.drop_table("factory_reel_schedules")
