"""عدة قنوات في المنصة الواحدة: post_deliveries.channel_remote_id وقيد فريد جديد

- post_deliveries.channel_remote_id: معرّف القناة الأصلي ("" = النشر القديم بمعرّف الإعدادات).
  الصفوف الموجودة تأخذ "" فتبقى صالحة ولا يُعاد نشرها.
- القيد الفريد يتغير من (post_id, platform) إلى (post_id, platform, channel_remote_id).
- posts.platforms يتسع إلى 255 حرفاً (يقبل "facebook:123,instagram:456").
خذ نسخة احتياطية من ملف القاعدة بختم زمني قبل التنفيذ.

Revision ID: b7e2c9a4d1f6
Revises: a9d3f7c1b2e4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7e2c9a4d1f6"
down_revision: Union[str, Sequence[str], None] = "a9d3f7c1b2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("post_deliveries", recreate="always") as batch:
        batch.add_column(sa.Column("channel_remote_id", sa.String(length=100), nullable=False, server_default=""))
        batch.drop_constraint("uq_post_deliveries_post_platform", type_="unique")
        batch.create_unique_constraint(
            "uq_post_deliveries_post_platform_channel", ["post_id", "platform", "channel_remote_id"],
        )
    with op.batch_alter_table("posts") as batch:
        batch.alter_column("platforms", existing_type=sa.String(length=100), type_=sa.String(length=255),
                           existing_nullable=True)


def downgrade() -> None:
    # الرجوع إلى قيد (post_id, platform) يستلزم صفاً واحداً لكل منصة: نُبقي أقدم صف ونحذف البقية
    op.execute(
        "DELETE FROM post_deliveries WHERE id NOT IN "
        "(SELECT MIN(id) FROM post_deliveries GROUP BY post_id, platform)"
    )
    with op.batch_alter_table("posts") as batch:
        batch.alter_column("platforms", existing_type=sa.String(length=255), type_=sa.String(length=100),
                           existing_nullable=True)
    with op.batch_alter_table("post_deliveries", recreate="always") as batch:
        batch.drop_constraint("uq_post_deliveries_post_platform_channel", type_="unique")
        batch.create_unique_constraint("uq_post_deliveries_post_platform", ["post_id", "platform"])
        batch.drop_column("channel_remote_id")
