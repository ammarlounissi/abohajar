"""مسودات النشر المجدول: الموعد الأصلي (للعودة إليه) ووقت الإلغاء (الملغاة تبقى وتُسترجع).

- post_drafts.planned_at: الموعد الذي وُزّعت عليه المسودة عند التوليد. القديمة تأخذ موعدها الحالي.
- post_drafts.cancelled_at: وقت حذفها أو إلغاء نشرها؛ لا تُحذف الصفوف بعد الآن."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4f1a8e2d6b3"
down_revision: Union[str, Sequence[str], None] = "b9e4c2d7a1f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("post_drafts") as batch:
        batch.add_column(sa.Column("planned_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("cancelled_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE post_drafts SET planned_at = scheduled_at WHERE planned_at IS NULL")
    op.execute("UPDATE post_drafts SET cancelled_at = updated_at WHERE status = 'cancelled' AND cancelled_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("post_drafts") as batch:
        batch.drop_column("cancelled_at")
        batch.drop_column("planned_at")
