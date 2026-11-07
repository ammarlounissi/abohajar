"""ربط قنوات النشر بالمستخدم الذي ربطها (social_channels.user_id)."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9b2c3d4e5f60"
down_revision: Union[str, Sequence[str], None] = "9a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("social_channels") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_social_channels_user_id", "users", ["user_id"], ["id"])
        batch.create_index("ix_social_channels_user_id", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("social_channels") as batch:
        batch.drop_index("ix_social_channels_user_id")
        batch.drop_constraint("fk_social_channels_user_id", type_="foreignkey")
        batch.drop_column("user_id")
