"""merge remaining heads 5fb08bda8a38 + d4e8a1b7c903

Revision ID: 44f0fc257cfa
Revises: 5fb08bda8a38, d4e8a1b7c903
Create Date: 2026-11-05 13:44:41.783390

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '44f0fc257cfa'
down_revision: Union[str, Sequence[str], None] = ('5fb08bda8a38', 'd4e8a1b7c903')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
