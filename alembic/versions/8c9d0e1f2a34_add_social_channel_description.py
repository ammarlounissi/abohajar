"""إضافة وصف مستقل لقنوات النشر."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8c9d0e1f2a34"
down_revision: Union[str, Sequence[str], None] = "7b8c9d0e1f23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("social_channels") as batch:
        batch.add_column(
            sa.Column("description", sa.Text(), nullable=False, server_default="")
        )

    # تنقل القنوات القديمة نشاط المصنع المرتبط بها إلى وصف القناة.
    # القنوات العامة بلا مصنع تبقى بوصف فارغ حتى يحدده المستخدم.
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE social_channels "
        "SET description = COALESCE(("
        "SELECT NULLIF(TRIM(f.activity), '') "
        "FROM factories AS f "
        "WHERE f.id = social_channels.factory_id"
        "), '') "
        "WHERE description = ''"
    ))


def downgrade() -> None:
    with op.batch_alter_table("social_channels") as batch:
        batch.drop_column("description")
