"""إشعارات المتصفح: اشتراكات الأجهزة في إشعارات لوحة الصفحة (push_subscriptions)، تابعة لمصنع الصفحة."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d7b3e9f1a2c4"
down_revision: Union[str, Sequence[str], None] = "c4f1a8e2d6b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("factory_id", sa.Integer(), sa.ForeignKey("factories.id"), nullable=False),
        sa.Column("service_key", sa.String(60), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("endpoint_sha256", sa.String(64), nullable=False),
        sa.Column("p256dh", sa.String(200), nullable=False),
        sa.Column("auth", sa.String(100), nullable=False),
        sa.Column("user_agent", sa.String(300), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_sent_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("factory_id", "endpoint_sha256", name="uq_push_subscriptions_factory_endpoint"),
    )
    op.create_index("ix_push_subscriptions_factory_id", "push_subscriptions", ["factory_id"])
    op.create_index("ix_push_subscriptions_endpoint_sha256", "push_subscriptions", ["endpoint_sha256"])


def downgrade() -> None:
    op.drop_index("ix_push_subscriptions_endpoint_sha256", table_name="push_subscriptions")
    op.drop_index("ix_push_subscriptions_factory_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
