"""persist AI provider for factory description jobs

Revision ID: c3a9f2e7b641
Revises: b2d6f0c4e8a3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3a9f2e7b641"
down_revision: Union[str, Sequence[str], None] = "b2d6f0c4e8a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "factory_description_jobs",
        sa.Column("provider", sa.String(length=20), server_default="ollama", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("factory_description_jobs", "provider")
