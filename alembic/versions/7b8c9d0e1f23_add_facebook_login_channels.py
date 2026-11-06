"""إضافة بيانات Facebook Login for Business إلى social_channels."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7b8c9d0e1f23"
down_revision: Union[str, Sequence[str], None] = "6a1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("social_channels") as batch:
        batch.add_column(sa.Column("page_id", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("page_name", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("page_access_token", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("owner_fbid", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("owner_name", sa.String(length=255), nullable=True))
        batch.alter_column(
            "factory_id",
            existing_type=sa.Integer(),
            nullable=True,
        )

    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE social_channels "
        "SET page_id = remote_id, page_name = name "
        "WHERE platform = 'facebook'"
    ))
    op.create_index(
        "uq_social_channels_page_id",
        "social_channels",
        ["page_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_social_channels_page_id", table_name="social_channels")
    with op.batch_alter_table("social_channels") as batch:
        batch.alter_column(
            "factory_id",
            existing_type=sa.Integer(),
            nullable=False,
        )
        batch.drop_column("owner_name")
        batch.drop_column("owner_fbid")
        batch.drop_column("page_access_token")
        batch.drop_column("page_name")
        batch.drop_column("page_id")
