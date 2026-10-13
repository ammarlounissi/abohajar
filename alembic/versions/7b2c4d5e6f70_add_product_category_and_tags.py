"""Add category and tags to products.

Revision ID: 7b2c4d5e6f70
Revises: 61fc53ee9c9b
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "7b2c4d5e6f70"
down_revision: Union[str, Sequence[str], None] = "61fc53ee9c9b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite requires a temporary server default when adding a non-null column
    # to a table that already contains rows.
    op.add_column(
        "products",
        sa.Column("category_code", sa.String(length=20), nullable=False, server_default="UNCATEGORIZED"),
    )
    op.add_column(
        "products",
        sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.create_index(op.f("ix_products_category_code"), "products", ["category_code"], unique=False)

    with op.batch_alter_table("products") as batch_op:
        batch_op.alter_column("category_code", server_default=None)
        batch_op.alter_column("tags", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_products_category_code"), table_name="products")
    with op.batch_alter_table("products") as batch_op:
        batch_op.drop_column("tags")
        batch_op.drop_column("category_code")
