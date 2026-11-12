"""مسودات النشر الجماعي المجدول للزبون وسجل الموافقة عليها.

- post_drafts: مسودة فيديو (القالب، الصوت، الصور، نص Gemini، الموعد) تُراجع قبل أن يُرندر شيء أو يُنشر.
- post_draft_approvals: سجل موافقات لا يُعدَّل (من، ومتى، وعلى أي نسخة)."""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b9e4c2d7a1f6"
down_revision: Union[str, Sequence[str], None] = "a3d7e9f1c2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "post_drafts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("factory_id", sa.Integer(), sa.ForeignKey("factories.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("template", sa.String(40), nullable=False),
        sa.Column("audio", sa.String(120), nullable=True),
        sa.Column("image_ids", sa.JSON(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("caption_job_id", sa.Integer(), sa.ForeignKey("factory_description_jobs.id"), nullable=True),
        sa.Column("caption_error", sa.Text(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("video_job_id", sa.Integer(), sa.ForeignKey("factory_video_jobs.id"), nullable=True),
        sa.Column("post_id", sa.Integer(), sa.ForeignKey("posts.id"), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_post_drafts_factory_id", "post_drafts", ["factory_id"])
    op.create_index("ix_post_drafts_status", "post_drafts", ["status"])
    op.create_index("ix_post_drafts_scheduled_at", "post_drafts", ["scheduled_at"])
    op.create_table(
        "post_draft_approvals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("draft_id", sa.Integer(), sa.ForeignKey("post_drafts.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("page_id", sa.String(100), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("caption_sha256", sa.String(64), nullable=False),
        sa.Column("template", sa.String(40), nullable=False),
        sa.Column("audio", sa.String(120), nullable=True),
        sa.Column("image_ids", sa.JSON(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_post_draft_approvals_draft_id", "post_draft_approvals", ["draft_id"])


def downgrade() -> None:
    op.drop_index("ix_post_draft_approvals_draft_id", table_name="post_draft_approvals")
    op.drop_table("post_draft_approvals")
    op.drop_index("ix_post_drafts_scheduled_at", table_name="post_drafts")
    op.drop_index("ix_post_drafts_status", table_name="post_drafts")
    op.drop_index("ix_post_drafts_factory_id", table_name="post_drafts")
    op.drop_table("post_drafts")
