"""وجهات المنشور بمعرّف القناة الداخلي: post_deliveries.channel_id بدل platform + channel_remote_id

- post_deliveries.channel_id (FK إلى social_channels.id، RESTRICT): القناة وحدها تعرف منصتها ومعرّفها الأصلي.
  القيد الفريد (post_id, channel_id) بدل (post_id, platform, channel_remote_id).
- حذف posts.platforms: الوجهات هي صفوف post_deliveries (لكل قناة صف، pending قبل النشر).

ترحيل البيانات:
  1. كل تسليم قديم يُربط بقناته؛ التسليم القديم بمعرّف الإعدادات ("" ) يُربط بقناة تُسجَّل من META_PAGE_ID
     أو META_IG_USER_ID إن لم تكن مسجلة (وإن لم يُضبط المعرّف تُسجَّل قناة معطّلة بمعرّف "0" كي يبقى السجل).
  2. كل منشور لم يُنشر بعد (draft/scheduled/approved/publishing/failed) يُحوَّل نص platforms القديم إلى وجهات
     pending بالقواعد القديمة نفسها: "facebook" = كل قنوات المنصة المفعّلة، و"facebook:123" = تلك القناة.
  3. قناة مذكورة في المنشور ولا وجود لها تُسجَّل معطّلة باسم "(مرحَّل)" فيفشل نشرها بسبب واضح بدل أن تُنشر بصمت.
خذ نسخة احتياطية من ملف القاعدة بختم زمني قبل التنفيذ. الرجوع (downgrade) تقريبي: يعيد النص من القنوات.

Revision ID: d3a8f1c5b7e9
Revises: c6f2a1d9e843
"""
import os
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3a8f1c5b7e9"
down_revision: Union[str, Sequence[str], None] = "c6f2a1d9e843"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PENDING_POST_STATUSES = ("DRAFT", "SCHEDULED", "APPROVED", "PUBLISHING", "FAILED")

channels = sa.table(
    "social_channels",
    sa.column("id", sa.Integer), sa.column("platform", sa.String), sa.column("name", sa.String),
    sa.column("remote_id", sa.String), sa.column("is_active", sa.Boolean), sa.column("created_at", sa.DateTime),
)
deliveries = sa.table(
    "post_deliveries",
    sa.column("id", sa.Integer), sa.column("post_id", sa.Integer), sa.column("platform", sa.String),
    sa.column("channel_remote_id", sa.String), sa.column("channel_id", sa.Integer), sa.column("status", sa.String),
    sa.column("attempts", sa.Integer),
)
posts = sa.table("posts", sa.column("id", sa.Integer), sa.column("status", sa.String), sa.column("platforms", sa.String))


def _legacy_remote_id(platform: str) -> str:
    return {"facebook": os.getenv("META_PAGE_ID", ""), "instagram": os.getenv("META_IG_USER_ID", "")}.get(platform, "").strip()


def _find(bind, platform: str, remote_id: str):
    return bind.execute(
        sa.select(channels.c.id).where(channels.c.platform == platform, channels.c.remote_id == remote_id)
    ).scalar()


def _ensure(bind, platform: str, remote_id: str, name: str, active: bool) -> int:
    found = _find(bind, platform, remote_id)
    if found:
        return found
    bind.execute(channels.insert().values(
        platform=platform, name=name, remote_id=remote_id, is_active=active, created_at=datetime.utcnow(),
    ))
    return _find(bind, platform, remote_id)


def _legacy_channel(bind, platform: str) -> int:
    remote = _legacy_remote_id(platform)
    return _ensure(bind, platform, remote or "0", f"{platform} (من الإعدادات)" if remote else f"{platform} (مرحَّل)", bool(remote))


def _migrate_data(bind) -> None:
    taken = set()       # (post_id, channel_id) المستعملة، لتفادي كسر القيد الفريد الجديد
    # صفوف سبق ربطها (تشغيل سابق انقطع في منتصفه): تبقى كما هي وتُحسب في التكرار
    for row in bind.execute(sa.select(deliveries.c.post_id, deliveries.c.channel_id).where(
        deliveries.c.channel_id.isnot(None),
    )).fetchall():
        taken.add((row.post_id, row.channel_id))
    for row in bind.execute(sa.select(
        deliveries.c.id, deliveries.c.post_id, deliveries.c.platform, deliveries.c.channel_remote_id,
    ).where(deliveries.c.channel_id.is_(None)).order_by(deliveries.c.id)).fetchall():
        remote = (row.channel_remote_id or "").strip()
        if remote:
            channel_id = _ensure(bind, row.platform, remote, f"{row.platform}:{remote} (مرحَّل)", False)
        else:
            channel_id = _legacy_channel(bind, row.platform)
        if (row.post_id, channel_id) in taken:
            bind.execute(deliveries.delete().where(deliveries.c.id == row.id))     # تكرار لنفس القناة
            continue
        taken.add((row.post_id, channel_id))
        bind.execute(deliveries.update().where(deliveries.c.id == row.id).values(channel_id=channel_id))

    pending = bind.execute(sa.select(posts.c.id, posts.c.platforms).where(
        posts.c.status.in_(PENDING_POST_STATUSES), posts.c.platforms.isnot(None),
    ).order_by(posts.c.id)).fetchall()
    for post in pending:
        wanted = []
        for token in (post.platforms or "").split(","):
            platform, _, remote = token.strip().partition(":")
            platform, remote = platform.strip().lower(), remote.strip()
            if not platform:
                continue
            if remote:
                wanted.append(_ensure(bind, platform, remote, f"{platform}:{remote} (مرحَّل)", False))
                continue
            rows = bind.execute(sa.select(channels.c.id, channels.c.is_active).where(
                channels.c.platform == platform).order_by(channels.c.id)).fetchall()
            active = [r.id for r in rows if r.is_active]
            wanted += active or [r.id for r in rows] or [_legacy_channel(bind, platform)]
        for channel_id in dict.fromkeys(wanted):
            if (post.id, channel_id) in taken:
                continue
            taken.add((post.id, channel_id))
            # العمودان القديمان ما زالا NOT NULL وقيدهما الفريد القديم قائم حتى نهاية الترحيل، فنملؤهما من
            # القناة نفسها (فريدان لكل قناة في المنشور الواحد) ثم يُحذفان في الخطوة التالية
            channel = bind.execute(sa.select(channels.c.platform, channels.c.remote_id).where(
                channels.c.id == channel_id)).one()
            bind.execute(deliveries.insert().values(
                post_id=post.id, channel_id=channel_id, platform=channel.platform,
                channel_remote_id=channel.remote_id, status="pending", attempts=0,
            ))


def _drop_leftover_tmp() -> None:
    """SQLite لا يتراجع عن DDL عند الفشل، فقد يبقى جدول مؤقت من تشغيل انقطع ويمنع إعادة المحاولة."""
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_post_deliveries")


def upgrade() -> None:
    """آمن لإعادة التشغيل بعد تشغيل انقطع في منتصفه: يتجاهل الخطوات المنفَّذة ويبني ما نقص."""
    bind = op.get_bind()
    _drop_leftover_tmp()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("post_deliveries")}
    if "channel_id" not in columns:
        with op.batch_alter_table("post_deliveries", recreate="always") as batch:
            batch.add_column(sa.Column("channel_id", sa.Integer(), nullable=True))
    # وجهات أنشأها تشغيل فاشل بمنصة فارغة (لا تُنتج إلا من هذا الترحيل): تُحذف وتُعاد من posts.platforms
    bind.execute(deliveries.delete().where(deliveries.c.platform == "", deliveries.c.channel_id.isnot(None)))

    _migrate_data(bind)
    _drop_leftover_tmp()

    with op.batch_alter_table("post_deliveries", recreate="always") as batch:
        batch.drop_constraint("uq_post_deliveries_post_platform_channel", type_="unique")
        batch.alter_column("channel_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("platform")
        batch.drop_column("channel_remote_id")
        batch.create_foreign_key(
            "fk_post_deliveries_channel_id", "social_channels", ["channel_id"], ["id"], ondelete="RESTRICT",
        )
        batch.create_unique_constraint("uq_post_deliveries_post_channel", ["post_id", "channel_id"])
        batch.create_index("ix_post_deliveries_channel_id", ["channel_id"])
    with op.batch_alter_table("posts") as batch:
        batch.drop_column("platforms")


def downgrade() -> None:
    with op.batch_alter_table("posts") as batch:
        batch.add_column(sa.Column("platforms", sa.String(length=255), nullable=True))
    with op.batch_alter_table("post_deliveries", recreate="always") as batch:
        batch.add_column(sa.Column("platform", sa.String(length=30), nullable=False, server_default=""))
        batch.add_column(sa.Column("channel_remote_id", sa.String(length=100), nullable=False, server_default=""))

    bind = op.get_bind()
    for row in bind.execute(sa.select(deliveries.c.id, deliveries.c.channel_id)).fetchall():
        channel = bind.execute(sa.select(channels.c.platform, channels.c.remote_id).where(
            channels.c.id == row.channel_id)).first()
        bind.execute(deliveries.update().where(deliveries.c.id == row.id).values(
            platform=channel.platform, channel_remote_id=channel.remote_id))
    for post in bind.execute(sa.select(posts.c.id)).fetchall():
        items = bind.execute(sa.select(deliveries.c.platform, deliveries.c.channel_remote_id).where(
            deliveries.c.post_id == post.id).order_by(deliveries.c.id)).fetchall()
        if items:
            bind.execute(posts.update().where(posts.c.id == post.id).values(
                platforms=",".join(f"{i.platform}:{i.channel_remote_id}" for i in items)))

    with op.batch_alter_table("post_deliveries", recreate="always") as batch:
        batch.drop_index("ix_post_deliveries_channel_id")
        batch.drop_constraint("uq_post_deliveries_post_channel", type_="unique")
        batch.drop_constraint("fk_post_deliveries_channel_id", type_="foreignkey")
        batch.drop_column("channel_id")
        batch.create_unique_constraint(
            "uq_post_deliveries_post_platform_channel", ["post_id", "platform", "channel_remote_id"],
        )
