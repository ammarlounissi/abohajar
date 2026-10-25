"""Link each social channel to its factory.

Revision ID: 4d8a6b2f1c90
Revises: 9c1e5a7d3b24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4d8a6b2f1c90"
down_revision: Union[str, Sequence[str], None] = "9c1e5a7d3b24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    has_channels = bind.execute(sa.text("SELECT 1 FROM social_channels LIMIT 1")).first() is not None
    has_default_factory = bind.execute(sa.text("SELECT 1 FROM factories WHERE id = 4")).first() is not None
    if has_channels and not has_default_factory:
        raise RuntimeError("Cannot assign existing social channels: factory id 4 does not exist")

    op.add_column("social_channels", sa.Column("factory_id", sa.Integer(), nullable=True))
    if has_channels:
        op.execute(sa.text("UPDATE social_channels SET factory_id = 4"))

    with op.batch_alter_table("social_channels") as batch:
        batch.alter_column("factory_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key(
            "fk_social_channels_factory_id_factories", "factories", ["factory_id"], ["id"],
        )
        batch.create_index("ix_social_channels_factory_id", ["factory_id"])


def downgrade() -> None:
    with op.batch_alter_table("social_channels") as batch:
        batch.drop_index("ix_social_channels_factory_id")
        batch.drop_constraint("fk_social_channels_factory_id_factories", type_="foreignkey")
        batch.drop_column("factory_id")
