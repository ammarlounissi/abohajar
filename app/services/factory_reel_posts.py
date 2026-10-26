"""إنشاء منشور ريل معتمد لمصنع من ناتج مهمتين منتهيتين: مهمة فيديو ومهمة وصف (Ollama).

المنشور يُنشأ approved جاهزاً لمؤقت النشر، بلا موعد (scheduled_at=None) فيلتقطه المؤقت فور إنشائه، وقنواته
هي كل قنوات المصنع المفعّلة. لا شبكة هنا ولا HTTP: دالة واحدة تنفّذ commit بنفسها (اتفاقية الخدمات)."""
import re
from typing import List

from sqlalchemy.orm import Session

from app import models
from app.core.time import utc_now
from app.models import DeliveryStatus, PostApproval, PostStatus
from app.services import post_publisher
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.factories import get_factory
from app.services.post_templates import BRAND_TAGS

MAX_EXTRA_TAGS = 4
_WORD = re.compile(r"[\w؀-ۿ]{3,}")
_HASHTAG = re.compile(r"#[\w؀-ۿ]+")


def suggested_hashtags(factory: models.Factory, description: str) -> List[str]:
    """وسوم العلامة + وسم باسم المصنع + كلمات من نشاطه، دون تكرار وسم موجود في نص الوصف."""
    name_tag = "#" + "_".join(_WORD.findall(factory.name))
    candidates = list(BRAND_TAGS) + [name_tag]
    candidates += ["#" + word for word in _WORD.findall(factory.activity or "")]
    present = set(_HASHTAG.findall(description))
    tags = list(dict.fromkeys(tag for tag in candidates if len(tag) > 1 and tag not in present))
    return tags[: len(BRAND_TAGS) + 1 + MAX_EXTRA_TAGS]


def build_description(factory: models.Factory, text: str) -> str:
    """رد Ollama + رقم هاتف المصنع + وسوم مقترحة."""
    body = text.strip()
    lines = [body, f"📲 للطلب أو الاستفسار عبر واتساب: {factory.phone_number}"]
    tags = suggested_hashtags(factory, body)
    if tags:
        lines.append(" ".join(tags))
    return "\n\n".join(lines)


def create_reel_post(db: Session, factory_id: int, video_id: int, chat_id: int) -> models.Post:
    """video_id: معرّف مهمة توليد الفيديو (factory_video_jobs)، وchat_id: معرّف مهمة الوصف (factory_description_jobs).
    كلتاهما يجب أن تتبع المصنع وتكون succeeded."""
    factory = get_factory(db, factory_id)

    video_job = db.query(models.FactoryVideoJob).filter(
        models.FactoryVideoJob.id == video_id, models.FactoryVideoJob.factory_id == factory_id,
    ).first()
    if video_job is None:
        raise NotFoundError("مهمة توليد الفيديو غير موجودة لهذا المصنع")
    if video_job.status != "succeeded" or not video_job.video_url:
        raise ConflictError(f"مهمة الفيديو لم تكتمل بعد (الحالة: {video_job.status})")

    chat_job = db.query(models.FactoryDescriptionJob).filter(
        models.FactoryDescriptionJob.id == chat_id, models.FactoryDescriptionJob.factory_id == factory_id,
    ).first()
    if chat_job is None:
        raise NotFoundError("مهمة توليد الوصف غير موجودة لهذا المصنع")
    if chat_job.status != "succeeded" or not (chat_job.response or "").strip():
        raise ConflictError(f"مهمة الوصف لم تكتمل بعد (الحالة: {chat_job.status})")

    channel_ids = post_publisher.active_channel_ids(db, factory_id)
    if not channel_ids:
        raise InvalidInputError("لا توجد قنوات نشر مفعّلة مرتبطة بهذا المصنع")

    post = models.Post(
        factory_id=factory_id,
        media_type="reel",
        media_urls=[video_job.video_url],
        description=build_description(factory, chat_job.response),
        status=PostStatus.APPROVED,
        approval=PostApproval.APPROVED,
        approved_at=utc_now(),
        scheduled_at=None,
        sku=video_job.sku,
    )
    post.deliveries = [
        models.PostDelivery(channel_id=channel_id, status=DeliveryStatus.PENDING.value, attempts=0)
        for channel_id in channel_ids
    ]
    try:
        db.add(post)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(post)
    return post
