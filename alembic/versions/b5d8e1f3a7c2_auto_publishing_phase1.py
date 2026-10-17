"""النشر التلقائي (المرحلة الأولى): أعمدة posts الناقصة + post_deliveries + publish_reports

- posts: أعمدة platforms وscheduled_at وapproved_at وclaimed_at وerror وsku،
  وتوسيع حالات status (approved, publishing, failed) وإضافة media_type = text.
  النص يبقى في العمود الموجود description (هو caption المنشور)، والموافقة تُضبط
  بـ status=approved (العمود القديم approval يبقى كما هو ولا يُحذف).
- post_deliveries: نتيجة كل منصة على حدة، قيد فريد على (post_id, platform).
- publish_reports: تقارير أخطاء النشر.

لا يُحوَّل أي صف موجود إلى approved: المنشورات القديمة لا تُنشر تلقائياً بالخطأ.
خذ نسخة احتياطية من ملف القاعدة بختم زمني قبل التنفيذ.

Revision ID: b5d8e1f3a7c2
Revises: f2a9c4d6b1e8
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b5d8e1f3a7c2"
down_revision: Union[str, Sequence[str], None] = "f2a9c4d6b1e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_STATUS = sa.Enum("DRAFT", "SCHEDULED", "PUBLISHED", "ARCHIVED", name="poststatus")
NEW_STATUS = sa.Enum(
    "DRAFT", "SCHEDULED", "APPROVED", "PUBLISHING", "PUBLISHED", "FAILED", "ARCHIVED", name="poststatus"
)
OLD_MEDIA = sa.Enum("image", "gallery", "reel", name="postmediatype")
NEW_MEDIA = sa.Enum("text", "image", "gallery", "reel", name="postmediatype")


def upgrade() -> None:
    with op.batch_alter_table("posts") as batch:
        batch.add_column(sa.Column("platforms", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("scheduled_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("approved_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("claimed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("error", sa.Text(), nullable=True))
        batch.add_column(sa.Column("sku", sa.String(length=50), nullable=True))
        batch.alter_column("status", existing_type=OLD_STATUS, type_=NEW_STATUS, existing_nullable=False)
        batch.alter_column("media_type", existing_type=OLD_MEDIA, type_=NEW_MEDIA, existing_nullable=False)
    op.create_index("ix_posts_sku", "posts", ["sku"], unique=False)

    op.create_table(
        "post_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("remote_id", sa.String(length=100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("post_id", "platform", name="uq_post_deliveries_post_platform"),
    )
    op.create_index("ix_post_deliveries_post_id", "post_deliveries", ["post_id"])

    op.create_table(
        "publish_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=True),
        sa.Column("platform", sa.String(length=30), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="unknown"),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_publish_reports_post_id", "publish_reports", ["post_id"])


def downgrade() -> None:
    op.drop_index("ix_publish_reports_post_id", table_name="publish_reports")
    op.drop_table("publish_reports")
    op.drop_index("ix_post_deliveries_post_id", table_name="post_deliveries")
    op.drop_table("post_deliveries")

    # صفوف بحالات/نوع جديد لا يعرفها المخطط القديم: نعيدها إلى أقرب قيمة قديمة قبل التضييق
    op.execute("UPDATE posts SET status = 'DRAFT' WHERE status IN ('APPROVED', 'PUBLISHING', 'FAILED')")
    op.execute("UPDATE posts SET media_type = 'image' WHERE media_type = 'text'")
    op.drop_index("ix_posts_sku", table_name="posts")
    with op.batch_alter_table("posts") as batch:
        batch.alter_column("media_type", existing_type=NEW_MEDIA, type_=OLD_MEDIA, existing_nullable=False)
        batch.alter_column("status", existing_type=NEW_STATUS, type_=OLD_STATUS, existing_nullable=False)
        for col in ("sku", "error", "claimed_at", "approved_at", "scheduled_at", "platforms"):
            batch.drop_column(col)
