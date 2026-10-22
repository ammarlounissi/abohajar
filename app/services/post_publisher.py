"""النشر التلقائي: اختيار المنشور الجاهز، حجزه، النشر على كل قناة، تحديث الحالات، وكتابة التقارير.

مستقلة عن القناة والـ HTTP: تُرجع بيانات (RunSummary) والمستدعي (jobs/) يطبعها.
وجهات المنشور هي صفوف post_deliveries: كل صف يشير إلى قناة من جدول social_channels بمعرّفها الداخلي
(المنصة ومعرّفها الأصلي وحالة التفعيل تُقرأ من القناة)، وتُحدَّد بـ set_channels عند الإنشاء أو التعديل.

تذكير: عمود status هو Enum(PostStatus) فيُخزَّن باسم العضو (APPROVED) لا بقيمته (approved)،
لذلك كل الاستعلامات هنا بالـ ORM وليس بنص SQL خام.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional

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
CANDIDATE_SCAN = 200      # أقصى عدد منشورات جاهزة يفحصها التشغيل الواحد بحثاً عن قنوات غير ممتلئة
APPROVABLE = (PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.FAILED)   # FAILED: لإعادة المحاولة
MANUALLY_PUBLISHABLE = APPROVABLE + (PostStatus.APPROVED,)
UNPUBLISHED = (PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.APPROVED, PostStatus.PUBLISHING, PostStatus.FAILED)


@dataclass
class PostOutcome:
    post_id: int
    status: str                                   # حالة المنشور النهائية (أو "dry-run")
    channels: List[str]                           # وصف كل قناة مستهدفة (facebook:123456)
    results: List[PublishResult] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)   # منصات نُشر عليها سابقاً فلم تُعَد


@dataclass
class RunSummary:
    stale_post_ids: List[int] = field(default_factory=list)   # عالقون في publishing (للمراجعة اليدوية)
    outcomes: List[PostOutcome] = field(default_factory=list)
    limited_channels: List[str] = field(default_factory=list)   # قنوات بلغت سقفها اليومي فتأجل منشوراتها
    dry_run: bool = False

    @property
    def daily_limit_reached(self) -> bool:
        return bool(self.limited_channels)

    @property
    def failures(self) -> int:
        return sum(1 for o in self.outcomes for r in o.results if not r.ok)


@dataclass(frozen=True)
class PostView:
    """ما يراه المحوّل من المنشور لقناة واحدة: نص القناة (إن وُجد) بدل نص المنشور العام."""
    id: int
    media_type: str
    media_urls: List[str]
    description: Optional[str]


def view_for(post: models.Post, delivery: models.PostDelivery) -> PostView:
    caption = delivery.caption if delivery.caption is not None else post.description
    return PostView(post.id, post.media_type, list(post.media_urls or []), caption)


def _label(delivery: models.PostDelivery) -> str:
    return delivery.channel.label


# ---------------------------------------------------------------- قنوات المنشور

SETTABLE = (PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.APPROVED, PostStatus.FAILED)


def active_channel_ids(db: Session, platform: Optional[str] = None) -> List[int]:
    """معرّفات القنوات المفعّلة (كلها أو لمنصة واحدة): الاختيار الافتراضي عند إنشاء منشور."""
    query = select(models.SocialChannel.id).where(models.SocialChannel.is_active.is_(True))
    if platform:
        query = query.where(models.SocialChannel.platform == platform)
    return list(db.scalars(query.order_by(models.SocialChannel.id)))


def set_channels(db: Session, post: models.Post, channel_ids: Iterable[int]) -> models.Post:
    """يجعل قنوات المنشور هي channel_ids بالضبط (بلا تكرار، وبترتيب الإدخال).
    - قناة جديدة تُنشأ لها وجهة pending، ويجب أن تكون موجودة ومفعّلة.
    - وجهة ناجحة (published) لا تُحذف أبداً: هي سجل ما نُشر، وتبقى حتى لو غابت عن القائمة.
    - وجهة pending أو failed غابت عن القائمة تُحذف.
    لا يُعدَّل منشور قيد النشر أو منشور أو مؤرشف."""
    if post.status not in SETTABLE:
        raise ConflictError(f"لا يمكن تعديل قنوات منشور حالته {post.status.value}")
    wanted = list(dict.fromkeys(int(c) for c in channel_ids))
    channels = {c.id: c for c in db.scalars(select(models.SocialChannel).where(models.SocialChannel.id.in_(wanted)))} if wanted else {}
    missing = [c for c in wanted if c not in channels]
    if missing:
        raise NotFoundError(f"قنوات غير موجودة: {', '.join(map(str, missing))}")
    current = {d.channel_id: d for d in post.deliveries}
    inactive = [c for c in wanted if c not in current and not channels[c].is_active]
    if inactive:
        raise InvalidInputError(f"قنوات معطّلة: {', '.join(map(str, inactive))}")
    for channel_id, delivery in current.items():
        if channel_id not in wanted and delivery.status != DeliveryStatus.PUBLISHED.value:
            post.deliveries.remove(delivery)
    for channel_id in wanted:
        if channel_id not in current:
            post.deliveries.append(models.PostDelivery(
                post_id=post.id, channel_id=channel_id, status=DeliveryStatus.PENDING.value, attempts=0,
            ))
    db.commit()
    db.refresh(post)
    return post


# ---------------------------------------------------------------- الموافقة

def approve_post(db: Session, post_id: int, now: Optional[datetime] = None) -> models.Post:
    """يجعل المنشور جاهزاً للنشر. يقبل draft وscheduled وfailed (إعادة محاولة المنصات الفاشلة فقط)."""
    post = db.get(models.Post, post_id)
    if not post:
        raise NotFoundError("المنشور غير موجود")
    if post.status not in APPROVABLE:
        raise ConflictError(f"لا يمكن اعتماد منشور حالته {post.status.value}")
    if not post.deliveries:
        raise InvalidInputError("حدّد قناة نشر واحدة على الأقل قبل اعتماد المنشور")
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
    if not post.deliveries:
        raise InvalidInputError("حدّد قناة نشر واحدة على الأقل قبل النشر")
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


def _due_ordered_query(now: datetime):
    return (
        select(models.Post.id)
        .where(*_next_due_filter(now))
        .order_by(func.coalesce(models.Post.scheduled_at, models.Post.approved_at), models.Post.id)
    )


def _next_due_id_query(now: datetime):
    return _due_ordered_query(now).limit(1)


def due_post_ids(db: Session, now: datetime, limit: int = CANDIDATE_SCAN) -> List[int]:
    """معرّفات المنشورات التي حان موعدها مرتبةً بالأقدم أولاً (دون حجزها)."""
    return list(db.scalars(_due_ordered_query(now).limit(limit)))


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


def claim_due(db: Session, post_id: int, now: datetime) -> Optional[models.Post]:
    """حجز ذري لمنشور بعينه بشرط أنه ما زال approved وحان موعده (None إن سبقه تشغيل آخر أو لم يعد جاهزاً)."""
    row = db.execute(
        update(models.Post)
        .where(models.Post.id == post_id, *_next_due_filter(now))
        .values(status=PostStatus.PUBLISHING, claimed_at=now)
        .returning(models.Post.id)
        .execution_options(synchronize_session=False)
    ).first()
    db.commit()
    if not row:
        return None
    db.expire_all()
    return db.get(models.Post, post_id)


def channel_daily_limit(platform: str) -> int:
    """سقف القناة في 24 ساعة: PUBLISH_DAILY_LIMIT_<PLATFORM> إن وُجد، وإلا PUBLISH_DAILY_LIMIT."""
    specific = getattr(settings, f"PUBLISH_DAILY_LIMIT_{platform.upper()}", None)
    return specific or settings.PUBLISH_DAILY_LIMIT


def channel_usage(db: Session, since: datetime) -> Dict[int, int]:
    """عدد ما نُشر فعلاً على كل قناة منذ since (من post_deliveries)."""
    rows = db.execute(
        select(models.PostDelivery.channel_id, func.count())
        .where(models.PostDelivery.status == DeliveryStatus.PUBLISHED.value,
               models.PostDelivery.published_at >= since)
        .group_by(models.PostDelivery.channel_id)
    )
    return {channel_id: n for channel_id, n in rows}


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

def _channel_problem(publishers: Dict[str, Publisher], channel: models.SocialChannel) -> Optional[PublishError]:
    """سبب يمنع النشر على القناة قبل أي طلب شبكة (معطّلة أو منصتها بلا محوّل)."""
    if not channel.is_active:
        return PublishError(ReportKind.UNKNOWN, f"channel_inactive:{channel.label}")
    if publishers.get(channel.platform) is None:
        return PublishError(ReportKind.UNKNOWN, f"unsupported_platform:{channel.platform}")
    return None


async def _publish_to_channel(publishers: Dict[str, Publisher], channel: models.SocialChannel, post: PostView) -> PublishResult:
    platform = channel.platform
    problem = _channel_problem(publishers, channel)
    if problem:
        result = PublishResult(platform, False, error=problem.message, kind=problem.kind)
    else:
        publisher = publishers[platform].for_channel(channel.remote_id)
        try:
            result = await publisher.publish(post)
        except PublishError as exc:
            result = PublishResult(platform, False, error=exc.message, kind=exc.kind)
        except Exception as exc:    # خلل غير متوقع في محوّل: لا نترك المنشور عالقاً
            logger.exception("publisher %s crashed for post %s", channel.label, post.id)
            result = PublishResult(platform, False, error=f"{type(exc).__name__}: {exc}"[:500], kind=ReportKind.UNKNOWN)
    result.channel_id = channel.id
    result.channel_label = channel.label
    return result


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
            post_id=post.id, platform=delivery.channel.platform, kind=kind.value,
            message=f"[{delivery.channel.remote_id}] {result.error or ''}", created_at=now,
        ))
    db.commit()


async def publish_post(db: Session, post: models.Post, publishers: Dict[str, Publisher], now: datetime) -> PostOutcome:
    """ينشر منشوراً محجوزاً على قنواته. القنوات التي نُشر عليها سابقاً لا تُعاد."""
    deliveries = list(post.deliveries)
    if not deliveries:
        post.status = PostStatus.FAILED
        post.error = "no_channels"
        db.add(models.PublishReport(post_id=post.id, kind=ReportKind.UNKNOWN.value, message="no_channels", created_at=now))
        db.commit()
        return PostOutcome(post.id, PostStatus.FAILED.value, [])

    outcome = PostOutcome(post.id, PostStatus.PUBLISHING.value, [_label(d) for d in deliveries])
    for delivery in deliveries:
        if delivery.status == DeliveryStatus.PUBLISHED.value:
            outcome.skipped.append(_label(delivery))
            continue
        delivery.status = DeliveryStatus.PUBLISHING.value
        delivery.attempts = (delivery.attempts or 0) + 1
        db.commit()
        result = await _publish_to_channel(publishers, delivery.channel, view_for(post, delivery))
        _record_result(db, post, delivery, result, datetime.utcnow())
        outcome.results.append(result)

    failed = [d for d in deliveries if d.status != DeliveryStatus.PUBLISHED.value]
    if failed:
        post.status = PostStatus.FAILED
        post.error = "; ".join(f"{_label(d)}: {d.error}" for d in failed)[:1000]
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
    per_run: Optional[int] = None,
) -> RunSummary:
    """تشغيل واحد: يُبلَّغ عن العالقين، ثم يُنشر حتى per_run (الافتراضي PUBLISH_PER_RUN) منشوراً حان موعده.
    السقف اليومي لكل قناة على حدة (channel_daily_limit) ويُحسب من النشر الفعلي في آخر 24 ساعة؛ المنشور الذي
    بلغت إحدى قنواته سقفها يُترك approved لتشغيل لاحق ولا يعطّل منشورات القنوات الأخرى."""
    now = now or datetime.utcnow()
    per_run = settings.PUBLISH_PER_RUN if per_run is None else per_run
    summary = RunSummary(dry_run=dry_run)
    summary.stale_post_ids = [] if dry_run else report_stale(db, now)
    if dry_run:
        summary.stale_post_ids = [
            p.id for p in db.scalars(select(models.Post).where(models.Post.status == PostStatus.PUBLISHING))
        ]

    usage = channel_usage(db, now - timedelta(hours=24))
    remaining: Dict[int, int] = {}

    def left(channel: models.SocialChannel) -> int:
        if channel.id not in remaining:
            remaining[channel.id] = channel_daily_limit(channel.platform) - usage.get(channel.id, 0)
        return remaining[channel.id]

    for post_id in due_post_ids(db, now):
        if len(summary.outcomes) >= max(0, per_run):
            break
        post = db.get(models.Post, post_id)
        pending = [d for d in post.deliveries if d.status != DeliveryStatus.PUBLISHED.value]
        full = [d.channel.label for d in pending if left(d.channel) <= 0]
        if full:
            summary.limited_channels.extend(label for label in full if label not in summary.limited_channels)
            continue
        for delivery in pending:
            remaining[delivery.channel_id] -= 1
        if dry_run:
            results = []
            for delivery in post.deliveries:
                channel = delivery.channel
                problem = _channel_problem(publishers, channel)
                if problem is None:
                    problem = publishers[channel.platform].for_channel(channel.remote_id).validate(view_for(post, delivery))
                results.append(PublishResult(
                    channel.platform, problem is None, error=problem.message if problem else None,
                    kind=problem.kind if problem else None, channel_id=channel.id, channel_label=channel.label,
                ))
            summary.outcomes.append(PostOutcome(post.id, "dry-run", [_label(d) for d in post.deliveries], results))
            continue
        claimed = claim_due(db, post_id, now)
        if not claimed:
            continue      # سبقه تشغيل آخر
        summary.outcomes.append(await publish_post(db, claimed, publishers, now))
    return summary
