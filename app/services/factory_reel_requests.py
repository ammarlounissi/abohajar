"""ريل كامل بمعرّف المصنع فقط: يطلب فيديو المكتبة العشوائي + وصف Ollama معاً، وعند نجاحهما ينشئ المنشور المعتمد.

المهمتان تنفذهما عاملتان خلفيتان منفصلتان، لذلك لا ينتظر الطلب بل يسجَّل في factory_reel_requests ويُنهى لاحقاً:
- العاملتان تستدعيان finalize_pending() بعد كل مهمة (فيكتمل الطلب دون أي متابعة).
- GET حالة الطلب يستدعي finalize_request() أيضاً كشبكة أمان.
الإنهاء متكرر الأمان: حجز pending -> finalizing بجملة UPDATE واحدة يمنع إنشاء منشورين لطلب واحد."""
import logging
from typing import Callable

from sqlalchemy.orm import Session

from app import models
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.services import factory_reel_posts, factory_video_jobs, ollama_chat, post_publisher
from app.services.errors import InvalidInputError, NotFoundError, ServiceError
from app.services.factories import get_factory

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


def enqueue_reel_request(db: Session, factory_id: int) -> models.FactoryReelRequest:
    factory = get_factory(db, factory_id)
    # فحوص سريعة قبل إنشاء أي مهمة كي لا تبقى مهمة يتيمة تستهلك Ollama أو ffmpeg بلا فائدة
    if not (factory.activity or "").strip():
        raise InvalidInputError("نشاط المصنع مطلوب لتوليد الوصف")
    if not post_publisher.active_channel_ids(db, factory_id):
        raise InvalidInputError("لا توجد قنوات نشر مفعّلة مرتبطة بهذا المصنع")
    video_job = factory_video_jobs.enqueue_automated_library_video(db, factory_id)   # يفحص الصور والصوت
    chat_job = ollama_chat.enqueue_description_job(db, factory_id)
    request = models.FactoryReelRequest(
        factory_id=factory_id, video_job_id=video_job.id, chat_job_id=chat_job.id, status="pending",
    )
    try:
        db.add(request)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(request)
    return request


def get_reel_request(db: Session, factory_id: int, request_id: int) -> models.FactoryReelRequest:
    request = db.query(models.FactoryReelRequest).filter(
        models.FactoryReelRequest.id == request_id, models.FactoryReelRequest.factory_id == factory_id,
    ).first()
    if request is None:
        raise NotFoundError("طلب الريل غير موجود لهذا المصنع")
    return request


def _close(db: Session, request: models.FactoryReelRequest, status: str, error: str = None, post_id: int = None):
    now = utc_now()
    request.status, request.error, request.post_id = status, error, post_id
    request.updated_at = request.completed_at = now
    db.commit()
    db.refresh(request)


def finalize_request(db: Session, request_id: int) -> models.FactoryReelRequest:
    """ينهي الطلب إن اكتملت مهمتاه (ينشئ المنشور) أو فشلت إحداهما (يغلقه فاشلاً). غير ذلك لا يغيّر شيئاً."""
    request = db.get(models.FactoryReelRequest, request_id)
    if request is None or request.status != "pending":
        return request
    db.refresh(request.video_job)
    db.refresh(request.chat_job)
    video, chat = request.video_job, request.chat_job

    failures = []
    if video.status == "failed":
        failures.append(f"فشل الفيديو: {video.error or 'سبب غير معروف'}")
    if chat.status == "failed":
        failures.append(f"فشل الوصف: {chat.error or 'سبب غير معروف'}")
    if failures:
        _close(db, request, "failed", "؛ ".join(failures))
        return request
    if video.status != "succeeded" or chat.status != "succeeded":
        return request

    claimed = db.query(models.FactoryReelRequest).filter(
        models.FactoryReelRequest.id == request_id, models.FactoryReelRequest.status == "pending",
    ).update({models.FactoryReelRequest.status: "finalizing"}, synchronize_session=False)
    db.commit()
    if claimed != 1:                      # عاملة أخرى سبقتنا
        db.refresh(request)
        return request
    try:
        post = factory_reel_posts.create_reel_post(db, request.factory_id, video.id, chat.id)
    except ServiceError as exc:
        db.rollback()
        _close(db, request, "failed", str(exc))
        logger.warning("Reel request %s failed: %s", request_id, exc)
    except Exception:
        db.rollback()
        logger.exception("Reel request %s failed unexpectedly", request_id)
        _close(db, request, "failed", "حدث خطأ أثناء إنشاء المنشور؛ راجع سجل العامل")
    else:
        _close(db, request, "succeeded", post_id=post.id)
        logger.info("Reel request %s created post %s", request_id, post.id)
    return request


def finalize_pending(session_factory: SessionFactory = SessionLocal) -> int:
    """تستدعيها العاملتان بعد كل مهمة: تنهي كل الطلبات المعلّقة الجاهزة. ترجع عدد ما أُغلق."""
    closed = 0
    with session_factory() as db:
        ids = [row[0] for row in db.query(models.FactoryReelRequest.id).filter(
            models.FactoryReelRequest.status == "pending",
        ).order_by(models.FactoryReelRequest.id)]
    for request_id in ids:
        with session_factory() as db:
            request = finalize_request(db, request_id)
            if request is not None and request.status in ("succeeded", "failed"):
                closed += 1
    return closed
