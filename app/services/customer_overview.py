"""ملخص رئيسية لوحة الزبون: مظهر صفحته كما في فيسبوك، وما ينتظره منه، وما أُنجز. لا تعرف شيئاً عن HTTP.

يقرأ من الخدمات القائمة ولا يغيّر شيئاً، عدا جلب نشاط الصفحة الفارغ من فيسبوك كما تفعل صفحة الإعدادات
(وإلا عُدّ «النشاط» ناقصاً ثم وُجد ممتلئاً عند فتحها)."""
from typing import List

from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.models import PostStatus
from app.services import (
    customer_captions, customer_workspaces, facebook_services, page_appearance, post_drafts, push_notifications,
)

# حالات المسودة كما تعرضها اللوحة (post_drafts._view)
_REVIEW = {"ready"}
_WRITING = {"writing"}
_UPCOMING = {"approved", "rendering", "scheduled", "publishing"}


def _missing_settings(factory: models.Factory) -> List[str]:
    """ما يُكتب في «إعدادات الصفحة» ولم يُكتب بعد، بترتيب الصفحة. الاسم لا ينقص (يأتي من فيسبوك)."""
    missing = []
    if not (factory.activity or "").strip():
        missing.append("activity")
    if not factory.phone_number:
        missing.append("whatsapp")
    if not factory.hashtags:
        missing.append("hashtags")
    return missing


def _published_total(db: Session, factory_id: int) -> int:
    return db.query(models.Post).filter(
        models.Post.factory_id == factory_id, models.Post.media_type == "reel",
        models.Post.status == PostStatus.PUBLISHED,
    ).count()


def _draft_post_ids(db: Session, factory_id: int) -> set:
    rows = db.query(models.PostDraft.post_id).filter(
        models.PostDraft.factory_id == factory_id, models.PostDraft.post_id.isnot(None),
    )
    return {post_id for (post_id,) in rows}


def overview(db: Session, user_id: int, service_key: str) -> dict:
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    factory_id = workspace.factory_id
    channel = facebook_services.connected_channel(db, user_id, service_key)
    customer_captions._fill_activity_from_facebook(db, user_id, service_key, workspace.factory)

    drafts = post_drafts.list_drafts(db, user_id, service_key, advance=False)["drafts"]
    upcoming = [draft for draft in drafts if draft["status"] in _UPCOMING]
    next_at = min((draft["scheduled_at"] for draft in upcoming), default=None)
    # منشورات الفيديو اليدوية (منشورات المسودات محسوبة مع المسودات)
    from_drafts = _draft_post_ids(db, factory_id)
    manual = [post for post in customer_workspaces.list_video_posts(db, user_id, service_key)
              if post["id"] not in from_drafts]

    look = page_appearance.page_appearance(workspace.page_id, channel.page_access_token)
    return {
        "page": {
            "page_id": workspace.page_id,
            "name": channel.page_name or channel.name or workspace.factory.name,
            "is_active": bool(channel.is_active),
            **look,
        },
        "missing_settings": _missing_settings(workspace.factory),
        "images": customer_workspaces._count(db, factory_id, "image"),
        "max_images": customer_workspaces.MAX_IMAGES,
        "videos": customer_workspaces._count(db, factory_id, "video"),
        "video_generating": customer_workspaces._active_job(db, factory_id) is not None,
        "drafts_to_review": sum(draft["status"] in _REVIEW for draft in drafts),
        "drafts_writing": sum(draft["status"] in _WRITING for draft in drafts),
        "scheduled": len(upcoming),
        "next_scheduled_at": next_at,
        "published": _published_total(db, factory_id),
        "failed_scheduled": sum(draft["status"] == "failed" for draft in drafts),
        "failed_videos": sum(post["status"] == "failed" for post in manual),
        "timezone": settings.PUBLISH_TIMEZONE,
        "notifications_available": push_notifications.available(),
        "notification_devices": push_notifications._count(db, factory_id),
    }
