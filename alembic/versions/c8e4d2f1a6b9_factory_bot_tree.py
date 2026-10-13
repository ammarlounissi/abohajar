"""Factory bot tree: bot_sessions, factories.categories, products.video_url

Revision ID: c8e4d2f1a6b9
Revises: a3c91e5b7d20
Create Date: 2026-10-10 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8e4d2f1a6b9'
down_revision: Union[str, Sequence[str], None] = 'a3c91e5b7d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('factories', sa.Column('categories', sa.JSON(), nullable=True))
    op.add_column('products', sa.Column('video_url', sa.String(length=500), nullable=True))
    op.create_table('bot_sessions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('factory_id', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=40), nullable=False),
    sa.Column('draft', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['factory_id'], ['factories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_bot_sessions_factory_id'), 'bot_sessions', ['factory_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_bot_sessions_factory_id'), table_name='bot_sessions')
    op.drop_table('bot_sessions')
    op.drop_column('products', 'video_url')
    op.drop_column('factories', 'categories')
