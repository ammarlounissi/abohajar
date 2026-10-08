"""Add pending_media_updates

Revision ID: a3c91e5b7d20
Revises: d17af55e7e4c
Create Date: 2026-10-08 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3c91e5b7d20'
down_revision: Union[str, Sequence[str], None] = 'd17af55e7e4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('pending_media_updates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('factory_id', sa.Integer(), nullable=False),
    sa.Column('sku', sa.String(length=50), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('price', sa.Float(), nullable=False),
    sa.Column('media_id', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['factory_id'], ['factories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pending_media_updates_factory_id'), 'pending_media_updates', ['factory_id'], unique=False)
    op.create_index(op.f('ix_pending_media_updates_sku'), 'pending_media_updates', ['sku'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_pending_media_updates_sku'), table_name='pending_media_updates')
    op.drop_index(op.f('ix_pending_media_updates_factory_id'), table_name='pending_media_updates')
    op.drop_table('pending_media_updates')
