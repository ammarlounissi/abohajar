"""النشر التلقائي: اختيار المنشور الجاهز، حجزه، النشر على كل منصة، تحديث الحالات، وكتابة التقارير.

مستقلة عن القناة والـ HTTP: تُرجع بيانات (RunSummary) والمستدعي (jobs/) يطبعها.
المنصات نص عادي في posts.platforms (مثل "facebook,instagram")، ولكل منصة صف في post_deliveries.

تذكير: عمود status هو Enum(PostStatus) فيُخزَّن باسم العضو (APPROVED) لا بقيمته (approved)،
لذلك كل الاستعلامات هنا بالـ ORM وليس بنص SQL خام.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import DeliveryStatus, PostStatus, ReportKind
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.social import build_publishers
from app.services.social.base import Publisher, PublishError, PublishResult

logger = logging.getLogger(__name__)

STALE_PREFIX = "stale_publishing"
APPROVABLE = (PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.FAILED)   # FAILED: لإعادة المحاولة
MANUALLY_PUBLISHABLE = APPROVABLE + (PostStatus.APPROVED,)
UNPUBLISHED = (PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.APPROVED, PostStatus.PUBLISHING, PostStatus.FAILED)


@dataclass
class PostOutcome:
    post_id: int
    status: str                                   # حالة المنشور النهائية (أو "dry-run")
    platforms: List[str]
    results: List[PublishResult] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)   # منصات نُشر عليها سابقاً فلم تُعَد


@dataclass
class RunSummary:
    stale_post_ids: List[int] = field(default_factory=list)   # عالقون في publishing (للمراجعة اليدوية)
    outcomes: List[PostOutcome] = field(default_factory=list)
    daily_limit_reached: bool = False
    dry_run: bool = False

    @property
    def failures(self) -> int:
        return sum(1 for o in self.outcomes for r in o.results if not r.ok)


def parse_platforms(value: Optional[str]) -> List[str]:
    """'Facebook, instagram,facebook' -> ['facebook', 'instagram'] (بلا تكرار وبالترتيب)."""
    seen: List[str] = []
    for item in (value or "").split(","):
        name = item.strip().lower()
        if name and name not in seen:
            seen.append(name)
    return seen


# ---------------------------------------------------------------- الموافقة

def approve_post(db: Session, post_id: int, now: Optional[datetime] = None) -> models.Post:
    """يجعل المنشور جاهزاً للنشر. يقبل draft وscheduled وfailed (إعادة محاولة المنصات الفاشلة فقط)."""
    post = db.get(models.Post, post_id)
    if not post:
        raise NotFoundError("المنشور غير موجود")
    if post.status not in APPROVABLE:
        raise ConflictError(f"لا يمكن اعتماد منشور حالته {post.status.value}")
    if not parse_platforms(post.platforms):
        raise InvalidInputError("حدّد المنصات (platforms) قبل اعتماد المنشور")
    post.status = PostStatus.APPROVED
    post.approval = models.PostApproval.APPROVED
    post.approved_at = now or datetime.utcnow()
    db.commit()
    db.refresh(post)
    return post


def list_posts(db: Session, status: Optional[PostStatus] = None, limit: int = 50) -> List[models.Post]:
    query = select(models.Post).order_by(models.Post.id.desc()).limit(limit)
    if status:
        query = query.where(models.Post.status == status)
    return list(db.scalars(query))


def list_published(db: Session, limit: int = 100, offset: int = 0) -> List[models.Post]:
    """المنشورات التي نُشرت (الأحدث نشراً أولاً)."""
    query = (select(models.Post).where(models.Post.status == PostStatus.PUBLISHED)
             .order_by(models.Post.published_at.desc(), models.Post.id.desc()).limit(limit).offset(offset))
    return list(db.scalars(query))


def list_unpublished(db: Session, limit: int = 100, offset: int = 0) -> List[models.Post]:
    """غير المنشورة: draft وscheduled وapproved وpublishing وfailed. المؤرشفة لا تدخل هنا ولا في المنشورة."""
    query = (select(models.Post).where(models.Post.status.in_(UNPUBLISHED))
             .order_by(models.Post.created_at.desc(), models.Post.id.desc()).limit(limit).offset(offset))
    return list(db.scalars(query))


# ---------------------------------------------------------------- النشر اليدوي

def claim_post(db: Session, post_id: int, now: Optional[datetime] = None) -> models.Post:
    """يحجز منشوراً بعينه للنشر الفوري (يتجاوز scheduled_at والموافقة والسقف اليومي).
    الحجز بجملة UPDATE واحدة فلا يتزامن مع المؤقت. المنشور المنشور أو قيد النشر يُرفض."""
    now = now or datetime.utcnow()
    post = db.get(models.Post, post_id)
    if not post:
        raise NotFoundError("المنشور غير موجود")
    if not parse_platforms(post.platforms):
        raise InvalidInputError("حدّد المنصات (platforms) قبل النشر")
    row = db.execute(
        update(models.Post)
        .where(models.Post.id == post_id, models.Post.status.in_(MANUALLY_PUBLISHABLE))
        .values(status=PostStatus.PUBLISHING, claimed_at=now)
        .returning(models.Post.id)
        .execution_options(synchronize_session=False)
    ).first()
    db.commit()
    if not row:
        db.refresh(post)
        raise ConflictError(f"لا يمكن نشر منشور حالته {post.status.value}")
    db.expire_all()
    return db.get(models.Post, post_id)


async def publish_claimed_in_background(post_id: int) -> None:
    """ينشر منشوراً سبق حجزه بجلسة خاصة (للاستخدام داخل BackgroundTasks؛ الريلز قد تستغرق دقائق).
    إن انقطع التنفيذ يبقى المنشور publishing فيُكشف كعالق في أول تشغيل مجدول."""
    db = SessionLocal()
    try:
        post = db.get(models.Post, post_id)
        if post and post.status == PostStatus.PUBLISHING:
            await publish_post(db, post, build_publishers(), datetime.utcnow())
    except Exception:
        logger.exception("manual publish of post %s crashed", post_id)
    finally:
        db.close()


# ---------------------------------------------------------------- الاختيار والحجز

def _next_due_filter(now: datetime):
    return (
        models.Post.status == PostStatus.APPROVED,
        or_(models.Post.scheduled_at.is_(None), models.Post.scheduled_at <= now),
    )


def _next_due_id_query(now: datetime):
    return (
        select(models.Post.id)
        .where(*_next_due_filter(now))
        .order_by(func.coalesce(models.Post.scheduled_at, models.Post.approved_at), models.Post.id)
        .limit(1)
    )


def peek_next_due(db: Session, now: datetime) -> Optional[models.Post]:
    """المنشور الذي سيُحجز لاحقاً دون حجزه (لـ --dry-run)."""
    post_id = db.scalar(_next_due_id_query(now))
    return db.get(models.Post, post_id) if post_id else None


def claim_next(db: Session, now: datetime) -> Optional[models.Post]:
    """حجز ذري بجملة UPDATE واحدة: لا يستطيع تشغيلان متزامنان حجز المنشور نفسه."""
    stmt = (
        update(models.Post)
        # correlate(None): بدونها يربط SQLAlchemy الاستعلام الفرعي بجدول posts في UPDATE فيفسد
        .where(models.Post.id == _next_due_id_query(now).correlate(None).scalar_subquery(),
               models.Post.status == PostStatus.APPROVED)
        .values(status=PostStatus.PUBLISHING, claimed_at=now)
        .returning(models.Post.id)
        .execution_options(synchronize_session=False)
    )
    row = db.execute(stmt).first()
    db.commit()
    if not row:
        return None
    db.expire_all()
    return db.get(models.Post, row[0])


def claimed_since(db: Session, since: datetime) -> int:
    return db.scalar(select(func.count()).select_from(models.Post).where(models.Post.claimed_at >= since)) or 0


# ---------------------------------------------------------------- العالقون

def report_stale(db: Session, now: datetime) -> List[int]:
    """منشور بقي في publishing أكثر من المهلة: لا يُعاد نشره (ربما نُشر فعلاً)، يُكتب تقريره مرة واحدة فقط."""
    cutoff = now - timedelta(minutes=settings.PUBLISH_STALE_MINUTES)
    stale = db.scalars(
        select(models.Post).where(
            models.Post.status == PostStatus.PUBLISHING,
            or_(models.Post.claimed_at.is_(None), models.Post.claimed_at < cutoff),
        )
    ).all()
    ids = []
    for post in stale:
        ids.append(post.id)
        already = db.scalar(
            select(func.count()).select_from(models.PublishReport).where(
                models.PublishReport.post_id == post.id,
                models.PublishReport.message.like(f"{STALE_PREFIX}%"),
                models.PublishReport.created_at >= (post.claimed_at or datetime.min),
            )
        )
        if not already:
            db.add(models.PublishReport(
                post_id=post.id, kind=ReportKind.UNKNOWN.value, created_at=now,
                message=f"{STALE_PREFIX}: claimed_at={post.claimed_at}; manual review needed (may have been published)",
            ))
    db.commit()
    return ids


# ---------------------------------------------------------------- النشر

def _get_or_create_deliveries(db: Session, post: models.Post, platforms: List[str]) -> Dict[str, models.PostDelivery]:
    existing = {d.platform: d for d in post.deliveries}
    for platform in platforms:
        if platform not in existing:
            delivery = models.PostDelivery(post_id=post.id, platform=platform, status=DeliveryStatus.PENDING.value, attempts=0)
            db.add(delivery)
            existing[platform] = delivery
    db.commit()
    return {p: existing[p] for p in platforms}


async def _publish_to_platform(publisher: Optional[Publisher], platform: str, post: models.Post) -> PublishResult:
    if publisher is None:
        return PublishResult(platform, False, error=f"unsupported_platform:{platform}", kind=ReportKind.UNKNOWN)
    try:
        return await publisher.publish(post)
    except PublishError as exc:
        return PublishResult(platform, False, error=exc.message, kind=exc.kind)
    except Exception as exc:    # خلل غير متوقع في محوّل: لا نترك المنشور عالقاً
        logger.exception("publisher %s crashed for post %s", platform, post.id)
        return PublishResult(platform, False, error=f"{type(exc).__name__}: {exc}"[:500], kind=ReportKind.UNKNOWN)


def _record_result(db: Session, post: models.Post, delivery: models.PostDelivery, result: PublishResult, now: datetime) -> None:
    if result.ok:
        delivery.status = DeliveryStatus.PUBLISHED.value
        delivery.remote_id = result.remote_id
        delivery.error = None
        delivery.published_at = now
    else:
        delivery.status = DeliveryStatus.FAILED.value
        delivery.error = result.error
        kind = result.kind or ReportKind.UNKNOWN
        db.add(models.PublishReport(
            post_id=post.id, platform=delivery.platform, kind=kind.value, message=result.error or "", created_at=now,
        ))
    db.commit()


async def publish_post(db: Session, post: models.Post, publishers: Dict[str, Publisher], now: datetime) -> PostOutcome:
    """ينشر منشوراً محجوزاً على منصاته. المنصات التي نُشر عليها سابقاً لا تُعاد."""
    platforms = parse_platforms(post.platforms)
    if not platforms:
        post.status = PostStatus.FAILED
        post.error = "no_platforms"
        db.add(models.PublishReport(post_id=post.id, kind=ReportKind.UNKNOWN.value, message="no_platforms", created_at=now))
        db.commit()
        return PostOutcome(post.id, PostStatus.FAILED.value, [])

    deliveries = _get_or_create_deliveries(db, post, platforms)
    outcome = PostOutcome(post.id, PostStatus.PUBLISHING.value, platforms)

    for platform in platforms:
        delivery = deliveries[platform]
        if delivery.status == DeliveryStatus.PUBLISHED.value:
            outcome.skipped.append(platform)
            continue
        delivery.status = DeliveryStatus.PUBLISHING.value
        delivery.attempts = (delivery.attempts or 0) + 1
        db.commit()
        result = await _publish_to_platform(publishers.get(platform), platform, post)
        _record_result(db, post, delivery, result, datetime.utcnow())
        outcome.results.append(result)

    failed = [d for d in deliveries.values() if d.status != DeliveryStatus.PUBLISHED.value]
    if failed:
        post.status = PostStatus.FAILED
        post.error = "; ".join(f"{d.platform}: {d.error}" for d in failed)[:1000]
    else:
        post.status = PostStatus.PUBLISHED
        post.published_at = datetime.utcnow()
        post.error = None
    db.commit()
    outcome.status = post.status.value
    return outcome


async def publish_due(
    db: Session,
    *,
    publishers: Dict[str, Publisher],
    dry_run: bool = False,
    now: Optional[datetime] = None,
    per_run: int = 1,
) -> RunSummary:
    """تشغيل واحد: يُبلَّغ عن العالقين، ثم يُنشر منشور واحد (per_run) إن وُجد جاهز وحان موعده.
    السقف اليومي (PUBLISH_DAILY_LIMIT) يُحسب من المنشورات المحجوزة في آخر 24 ساعة."""
    now = now or datetime.utcnow()
    summary = RunSummary(dry_run=dry_run)
    summary.stale_post_ids = [] if dry_run else report_stale(db, now)
    if dry_run:
        summary.stale_post_ids = [
            p.id for p in db.scalars(select(models.Post).where(models.Post.status == PostStatus.PUBLISHING))
        ]

    budget = settings.PUBLISH_DAILY_LIMIT - claimed_since(db, now - timedelta(hours=24))
    for _ in range(max(0, per_run)):
        if budget <= 0:
            summary.daily_limit_reached = True
            break
        if dry_run:
            post = peek_next_due(db, now)
            if not post:
                break
            platforms = parse_platforms(post.platforms)
            results = []
            for platform in platforms:
                publisher = publishers.get(platform)
                problem = (
                    PublishError(ReportKind.UNKNOWN, f"unsupported_platform:{platform}") if publisher is None
                    else publisher.validate(post)
                )
                results.append(PublishResult(platform, problem is None, error=problem.message if problem else None,
                                             kind=problem.kind if problem else None))
            summary.outcomes.append(PostOutcome(post.id, "dry-run", platforms, results))
            break      # لا حجز في التجربة، فلن يتغير المنشور التالي
        post = claim_next(db, now)
        if not post:
            break
        budget -= 1
        summary.outcomes.append(await publish_post(db, post, publishers, now))
    return summary
