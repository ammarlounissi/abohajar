"""Swap the physical products.title and products.category_code column names.

The values remain with their original data while the SQL column names are exchanged.
The ORM attributes continue mapping product titles and category codes to their original
values, so application behavior remains unchanged.

Revision ID: 9f3c2a7b1d60
Revises: e7c2a9d4f1b3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9f3c2a7b1d60"
down_revision: Union[str, Sequence[str], None] = "e7c2a9d4f1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_products_category_code", table_name="products")
    op.alter_column(
        "products", "category_code", new_column_name="_category_code_swap",
        existing_type=sa.String(length=20), existing_nullable=False,
    )
    op.alter_column(
        "products", "title", new_column_name="category_code",
        existing_type=sa.String(length=255), existing_nullable=False,
    )
    op.alter_column(
        "products", "_category_code_swap", new_column_name="title",
        existing_type=sa.String(length=20), existing_nullable=False,
    )
    op.create_index("ix_products_category_code", "products", ["title"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_products_category_code", table_name="products")
    op.alter_column(
        "products", "title", new_column_name="_category_code_swap",
        existing_type=sa.String(length=20), existing_nullable=False,
    )
    op.alter_column(
        "products", "category_code", new_column_name="title",
        existing_type=sa.String(length=255), existing_nullable=False,
    )
    op.alter_column(
        "products", "_category_code_swap", new_column_name="category_code",
        existing_type=sa.String(length=20), existing_nullable=False,
    )
    op.create_index("ix_products_category_code", "products", ["category_code"], unique=False)
