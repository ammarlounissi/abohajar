"""merge factory bot and posts branches

Revision ID: 61fc53ee9c9b
Revises: c8e4d2f1a6b9, e4f6a7b8c9d0
Create Date: 2026-10-13 09:18:05.949693

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '61fc53ee9c9b'
down_revision: Union[str, Sequence[str], None] = ('c8e4d2f1a6b9', 'e4f6a7b8c9d0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
