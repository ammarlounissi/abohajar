"""جدول service_connections: صفحة واحدة لكل (مستخدم، خدمة) عبر Facebook Login."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4c7e1d9b3f5"
down_revision: Union[str, Sequence[str], None] = "9b2c3d4e5f60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "service_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("service", sa.String(length=50), nullable=False),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("social_channels.id"), nullable=True),
        sa.Column("pending_pages", sa.Text(), nullable=True),
        sa.Column("pending_user_token", sa.Text(), nullable=True),
        sa.Column("pending_owner_name", sa.String(length=255), nullable=True),
        sa.Column("pending_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "service", name="uq_service_connections_user_service"),
    )
    op.create_index("ix_service_connections_id", "service_connections", ["id"])
    op.create_index("ix_service_connections_user_id", "service_connections", ["user_id"])
    op.create_index("ix_service_connections_channel_id", "service_connections", ["channel_id"])


def downgrade() -> None:
    op.drop_index("ix_service_connections_channel_id", table_name="service_connections")
    op.drop_index("ix_service_connections_user_id", table_name="service_connections")
    op.drop_index("ix_service_connections_id", table_name="service_connections")
    op.drop_table("service_connections")
