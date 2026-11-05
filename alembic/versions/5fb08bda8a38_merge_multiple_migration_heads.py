"""merge multiple migration heads

Revision ID: 5fb08bda8a38
Revises: a3c91e5b7d20, d142b0541761
Create Date: 2026-10-08 15:10:09.015807

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5fb08bda8a38'
down_revision: Union[str, Sequence[str], None] = ('a3c91e5b7d20', 'd142b0541761')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
