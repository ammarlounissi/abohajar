"""add drive synced files

Revision ID: b2d6f0c4e8a3
Revises: a1c5e9b3d7f2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2d6f0c4e8a3"
down_revision: Union[str, Sequence[str], None] = "a1c5e9b3d7f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "drive_synced_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("factory_id", sa.Integer(), sa.ForeignKey("factories.id"), nullable=False),
        sa.Column("drive_file_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("factory_id", "drive_file_id", name="uq_drive_synced_files_factory_file"),
    )
    op.create_index("ix_drive_synced_files_factory_id", "drive_synced_files", ["factory_id"])


def downgrade() -> None:
    op.drop_index("ix_drive_synced_files_factory_id", table_name="drive_synced_files")
    op.drop_table("drive_synced_files")
