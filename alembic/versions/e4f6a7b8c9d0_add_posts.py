"""Add posts for social media publishing.

Revision ID: e4f6a7b8c9d0
Revises: a3c91e5b7d20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e4f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "a3c91e5b7d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "posts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("factory_id", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.Enum("image", "gallery", "reel", name="postmediatype"), nullable=False),
        sa.Column("media_urls", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("DRAFT", "SCHEDULED", "PUBLISHED", "ARCHIVED", name="poststatus"), nullable=False),
        sa.Column("approval", sa.Enum("PENDING", "APPROVED", "REJECTED", name="postapproval"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["factory_id"], ["factories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_posts_id"), "posts", ["id"], unique=False)
    op.create_index(op.f("ix_posts_factory_id"), "posts", ["factory_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_posts_factory_id"), table_name="posts")
    op.drop_index(op.f("ix_posts_id"), table_name="posts")
    op.drop_table("posts")
