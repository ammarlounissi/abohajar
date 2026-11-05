"""فصل استقبال البوت عن تفعيل النشر في القنوات الاجتماعية.

الصفحات الموجودة تحتفظ بالسلوك السابق: الصفحة ذات is_active=true تستقبل البوت،
والصفحة المعطلة لا تستقبله. بعد ذلك يصبح bot_enabled مستقلًا عن is_active.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6a1b2c3d4e5f"
down_revision: Union[str, Sequence[str], None] = "44f0fc257cfa"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("social_channels") as batch:
        batch.add_column(
            sa.Column("bot_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )

    # لا نغيّر سلوك الصفحات المسجلة قبل هذا الترحيل.
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE social_channels "
        "SET bot_enabled = CASE WHEN is_active = 1 THEN 1 ELSE 0 END"
    ))


def downgrade() -> None:
    with op.batch_alter_table("social_channels") as batch:
        batch.drop_column("bot_enabled")
