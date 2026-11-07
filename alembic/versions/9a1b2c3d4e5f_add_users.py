"""إضافة جدول users: الدخول بفيسبوك أو بالبريد وكلمة السر."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9a1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "8c9d0e1f2a34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("facebook_id", sa.String(length=100), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="user"),
        sa.Column("factory_id", sa.Integer(), sa.ForeignKey("factories.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.CheckConstraint(
            "email IS NOT NULL OR facebook_id IS NOT NULL", name="ck_users_has_login_method"
        ),
        sa.CheckConstraint(
            "password_hash IS NULL OR email IS NOT NULL", name="ck_users_password_needs_email"
        ),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_facebook_id", "users", ["facebook_id"], unique=True)
    op.create_index("ix_users_factory_id", "users", ["factory_id"])


def downgrade() -> None:
    op.drop_index("ix_users_factory_id", table_name="users")
    op.drop_index("ix_users_facebook_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
