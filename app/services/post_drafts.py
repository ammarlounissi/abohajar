"""النشر الجماعي المجدول للزبون: «توليد 30 مسودة» بضغطة واحدة، ثم مراجعتها والموافقة عليها. لا تعرف شيئاً عن HTTP.

المبدأ (ما تطلبه Meta في المراجعة): لا يُنشر شيء لم يره صاحب الصفحة ويوافق عليه.
1. التوليد: كل مسودة تُحفظ بما سيُنشر فعلاً: القالب، الصوت، صور المكتبة بترتيبها، نص Gemini كاملاً، والموعد.
   المواعيد تُوزَّع آلياً: منشور واحد في اليوم ابتداءً من الغد، في الساعة التي يختارها الزبون.
2. المراجعة: الواجهة تعرض كل مسودة كمنشور فيسبوك بمعاينة Remotion (المكوّن نفسه الذي يرندر به الخادم).
   الزبون يعدّل النص، أو يعيد كتابته، أو يغيّر الموعد، أو يحذف المسودة.
3. الموافقة: تُسجَّل في post_draft_approvals (من، ومتى، وعلى أي نسخة من النص بالضبط).
4. التجهيز: قبل الموعد بـ RENDER_AHEAD يُطلب الفيديو من طابور الفيديو الموجود (بالصور والصوت والقالب
   المعتمدة)، وحين يكتمل يُنشأ منشور معتمد بموعده (posts.scheduled_at)، فينشره مؤقت النشر الموجود
   (hadjret-publish.timer) حين يحين.

النصوص تُكتب واحداً بعد واحد (لا 30 مهمة دفعة واحدة): كل وصف يرى ما قبله فلا تتكرر الافتتاحيات
(ollama_chat يبني قائمة التجنّب وأسلوب الافتتاح من الأوصاف المحفوظة). advance_* تتقدم بالمسودات وهي متكررة
الأمان؛ يستدعيها عاملا الفيديو والأوصاف بعد كل مهمة (وعامل الفيديو دورياً)، وقراءة المسودات كشبكة أمان.

قيود الجدولة (حتى لا تعدّ فيسبوك الصفحة مزعجة): منشوران في اليوم على الأكثر، و3 ساعات بين منشورين،
والموعد بعد ساعة من الآن على الأقل وفي حدود 90 يوماً."""
import hashlib
import logging
import random
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Iterable, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.models import DeliveryStatus, PostApproval, PostStatus
from app.services import (
    customer_captions, customer_workspaces, facebook_services, factory_video_jobs, media_library, ollama_chat,
    post_publisher, push_notifications, video_maker,
)
from app.services.errors import InvalidInputError, NotFoundError, ServiceError

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]

MAX_BATCH = 30                       # أقصى ما يُولَّد بضغطة واحدة
MAX_OPEN = 30                        # أقصى عدد مسودات بانتظار المراجعة (تُكتب أو جاهزة)
MAX_PER_DAY = 2
MIN_GAP = timedelta(hours=3)
MIN_LEAD = timedelta(hours=1)
MAX_AHEAD = timedelta(days=90)
RENDER_AHEAD = timedelta(hours=24)   # يُرندر الفيديو قبل موعده بهذا القدر (لا 30 فيديو دفعة واحدة)
LATE_LIMIT = timedelta(hours=6)      # إن لم يجهز الفيديو قبل موعده بهذا التأخير يفشل بدل نشر متأخر جداً
PUBLISHED_VISIBLE = timedelta(days=30)

WRITING, READY, APPROVED, RENDERING, SCHEDULED, FAILED, CANCELLED = (
    "writing", "ready", "approved", "rendering", "scheduled", "failed", "cancelled",
)
_PENDING_REVIEW = (WRITING, READY)
_TAKES_A_SLOT = (WRITING, READY, APPROVED, RENDERING, SCHEDULED)
_ACTIVE_JOB = ("queued", "running")
_FINALIZING = "finalizing"            # حجز قصير بين اكتمال الفيديو وإنشاء المنشور (يمنع منشورين)

LATE_MESSAGE = "فات موعد النشر قبل أن يجهز الفيديو. اختر موعداً جديداً ليُجهَّز من جديد."
NO_PAGE_MESSAGE = "الصفحة لم تعد مربوطة بالخدمة. أعد ربطها ثم اختر موعداً جديداً."


def tz() -> ZoneInfo:
    return ZoneInfo(settings.PUBLISH_TIMEZONE)


def _local_day(moment: datetime) -> date:
    return moment.replace(tzinfo=timezone.utc).astimezone(tz()).date()


def _as_utc(moment: datetime) -> datetime:
    """datetime بمنطقة زمنية -> UTC بلا tzinfo (كما تُخزَّن الأوقات في القاعدة)."""
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def rules() -> dict:
    return {
        "max_batch": MAX_BATCH,
        "max_open": MAX_OPEN,
        "max_per_day": MAX_PER_DAY,
        "min_gap_hours": int(MIN_GAP.total_seconds() // 3600),
        "min_lead_minutes": int(MIN_LEAD.total_seconds() // 60),
        "max_ahead_days": MAX_AHEAD.days,
        "timezone": settings.PUBLISH_TIMEZONE,
    }


# ───── المواعيد ─────

def _occupied(db: Session, factory_id: int, *, exclude_draft: Optional[int] = None) -> List[datetime]:
    """مواعيد منشورات الصفحة القائمة: المسودات النشطة، ومنشورات الريل المجدولة أو المنشورة حديثاً (من خارج
    المسودات، كالنشر الفوري)، فلا يتجاوز يومٌ حدّه ولا يتقارب منشوران."""
    now = utc_now()
    drafts = db.query(models.PostDraft).filter(
        models.PostDraft.factory_id == factory_id, models.PostDraft.status.in_(_TAKES_A_SLOT),
    )
    times, linked = [], set()
    for draft in drafts:
        if draft.post_id:
            linked.add(draft.post_id)
        if draft.id != exclude_draft:
            times.append(draft.scheduled_at)
    posts = db.query(models.Post).filter(
        models.Post.factory_id == factory_id, models.Post.media_type == "reel",
        models.Post.status.in_((PostStatus.APPROVED, PostStatus.PUBLISHING, PostStatus.PUBLISHED)),
    )
    for post in posts:
        if post.id in linked:
            continue
        moment = post.published_at or post.scheduled_at
        if moment is not None and moment >= now - timedelta(days=2):
            times.append(moment)
    return times


def check_slot(when: datetime, occupied: Iterable[datetime], now: Optional[datetime] = None) -> None:
    """InvalidInputError برسالة للزبون إن كان الموعد (UTC) لا يحترم قيود الجدولة."""
    now = now or utc_now()
    if when < now + MIN_LEAD:
        raise InvalidInputError("اختر موعداً بعد ساعة من الآن على الأقل")
    if when > now + MAX_AHEAD:
        raise InvalidInputError(f"اختر موعداً في حدود {MAX_AHEAD.days} يوماً من الآن")
    occupied = list(occupied)
    day = _local_day(when)
    if sum(1 for other in occupied if _local_day(other) == day) >= MAX_PER_DAY:
        raise InvalidInputError(
            f"في هذا اليوم {MAX_PER_DAY} منشوران مجدولان. اختر يوماً آخر: كثرة النشر في يوم واحد "
            "تجعل فيسبوك يعدّها إزعاجاً."
        )
    hours = int(MIN_GAP.total_seconds() // 3600)
    if any(abs(other - when) < MIN_GAP for other in occupied):
        raise InvalidInputError(f"بين منشورين {hours} ساعات على الأقل. اختر وقتاً أبعد عن المنشور الآخر.")


def plan_slots(count: int, at: time, occupied: Iterable[datetime], now: Optional[datetime] = None) -> List[datetime]:
    """count موعداً (UTC): واحد في اليوم في الساعة at محلياً، ابتداءً من الغد، تُتخطّى الأيام التي فيها منشور."""
    now = now or utc_now()
    zone = tz()
    busy_days = {_local_day(moment) for moment in occupied}
    day = now.replace(tzinfo=timezone.utc).astimezone(zone).date() + timedelta(days=1)
    slots: List[datetime] = []
    while len(slots) < count:
        if day not in busy_days:
            slots.append(_as_utc(datetime.combine(day, at, tzinfo=zone)))
        day += timedelta(days=1)
        if day > (now + MAX_AHEAD).date():
            raise InvalidInputError("لا مكان في الأشهر الثلاثة القادمة لهذا العدد من المنشورات؛ اطلب عدداً أقل")
    return slots


SLOT_STEP = timedelta(minutes=15)


def earliest_slot(occupied: Iterable[datetime], now: Optional[datetime] = None) -> datetime:
    """أقرب موعد (UTC) يحترم قيود الجدولة: بعد ساعة على الأقل (على رأس ربع ساعة)، ما لم يبلغ اليوم منشورين
    وكان بعيداً 3 ساعات عن كل منشور، في أي ساعة. هو «انشر في أقرب وقت» بعد إلغاء النشر الفوري."""
    now = now or utc_now()
    occupied = list(occupied)
    start = now + MIN_LEAD
    candidate = start.replace(second=0, microsecond=0)
    if candidate < start:
        candidate += timedelta(minutes=1)
    candidate += timedelta(minutes=(-candidate.minute) % 15)
    while candidate <= now + MAX_AHEAD:
        try:
            check_slot(candidate, occupied, now)
            return candidate
        except InvalidInputError:
            pass
        candidate += SLOT_STEP
    raise InvalidInputError(f"لا يوجد موعد متاح في حدود {MAX_AHEAD.days} يوماً")


# ───── التوليد ─────

def _rotation(items: list, count: int) -> list:
    """count عنصراً من items بالتناوب على ترتيب عشوائي (لا يتكرر عنصر قبل أن تُستنفد البقية)."""
    result: list = []
    while items and len(result) < count:
        result.extend(random.sample(items, len(items)))
    return result[:count]


def images_label(count: int) -> str:
    """عدد الصور بصيغته العربية: صورة واحدة، صورتان، 3–10 صور، 11 صورة فما فوق."""
    if count == 1:
        return "صورة واحدة"
    if count == 2:
        return "صورتان"
    return f"{count} صور" if 3 <= count <= 10 else f"{count} صورة"


# عدد صور المنشور = عدد صور قالبه دائماً (طلب المستخدم 2026-11-13، كتطبيقات الفيديو من القوالب): لا تُحذف صورة
# بل تُستبدل. القالب الذي يحتاج صوراً أكثر مما في المكتبة لا يُختار.
MIN_IMAGES = min(template.max_images for template in video_maker.VIDEO_TEMPLATES)


def _fitting_templates(library_size: int) -> list:
    return [template for template in video_maker.VIDEO_TEMPLATES if template.max_images <= library_size]


def _needs_more_images(library_size: int) -> InvalidInputError:
    return InvalidInputError(
        f"في مكتبتك {images_label(library_size)}، وأقصر قالب يحتاج {images_label(MIN_IMAGES)}. "
        "ارفع صوراً أكثر إلى المكتبة أولاً"
    )


def _image_sets(image_ids: List[int], sizes: List[int]) -> List[List[int]]:
    """صور كل مسودة: تُؤخذ تباعاً من سلسلة خلطات للمكتبة، فتتوزع الصور على المسودات ولا تتشابه المتتالية.
    القوالب تُختار بقدر المكتبة، فتأخذ كل مسودة عدد صور قالبها كاملاً."""
    sets, stream = [], []
    for size in sizes:
        size = min(size, len(image_ids))
        while len(stream) < size:
            stream.extend(random.sample(image_ids, len(image_ids)))
        chosen: List[int] = []
        rest: List[int] = []
        for media_id in stream:                      # بلا تكرار داخل المسودة الواحدة
            (rest if media_id in chosen or len(chosen) == size else chosen).append(media_id)
        sets.append(chosen)
        stream = rest
    return sets


def _count_pending(db: Session, factory_id: int) -> int:
    return db.query(models.PostDraft).filter(
        models.PostDraft.factory_id == factory_id, models.PostDraft.status.in_(_PENDING_REVIEW),
    ).count()


def _prepare(db: Session, user_id: int, service_key: str, count: int) -> tuple:
    """ما يلزم قبل إنشاء count منشوراً: نشاط الصفحة (منه يُكتب النص)، وصور المكتبة، ومكان بين المسودات."""
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    factory = workspace.factory
    customer_captions._fill_activity_from_facebook(db, user_id, service_key, factory)
    if not (factory.activity or "").strip():
        raise InvalidInputError("اكتب نشاط صفحتك في «إعدادات الصفحة» أولاً: منه يكتب الذكاء الاصطناعي النصوص")
    image_ids = _library_images(db, factory.id)
    if not image_ids:
        raise InvalidInputError("ارفع صوراً إلى المكتبة أولاً لتُبنى منها الفيديوهات")
    if len(image_ids) < MIN_IMAGES:
        raise _needs_more_images(len(image_ids))
    pending = _count_pending(db, factory.id)
    if pending + count > MAX_OPEN:
        left = MAX_OPEN - pending
        raise InvalidInputError(
            f"لديك {pending} مسودة بانتظار المراجعة. راجعها أو احذف بعضها أولاً"
            + (f"، أو اطلب {left} على الأكثر." if left > 0 else ".")
        )
    return factory, image_ids


def _library_images(db: Session, factory_id: int) -> List[int]:
    return [row[0] for row in db.query(models.Media.id).filter(
        models.Media.factory_id == factory_id, models.Media.media_type == "image",
    ).order_by(models.Media.id)]


def _new_drafts(db: Session, factory: models.Factory, user_id: int, image_ids: List[int], slots: List[datetime]) -> list:
    """مسودات بقوالب وأصوات وصور متناوبة في المواعيد slots، تبدأ بكتابة نصوصها (writing)."""
    count = len(slots)
    templates = _rotation(_fitting_templates(len(image_ids)), count)
    audio = _rotation(video_maker.list_audio_files(), count) or [None] * count
    images = _image_sets(image_ids, [template.max_images for template in templates])
    drafts = [
        models.PostDraft(
            factory_id=factory.id, user_id=user_id, status=WRITING, template=templates[index].id,
            audio=audio[index], image_ids=images[index], scheduled_at=slots[index], planned_at=slots[index],
        )
        for index in range(count)
    ]
    db.add_all(drafts)
    db.commit()
    advance_captions(db, factory.id)
    return drafts


def generate(db: Session, user_id: int, service_key: str, *, count: int, at: str) -> dict:
    """ينشئ count مسودة بقوالب وأصوات وصور متناوبة ومواعيد يومية، ويبدأ كتابة نصوصها بـ Gemini واحداً بعد واحد."""
    factory, image_ids = _prepare(db, user_id, service_key, count)
    hour, minute = (int(part) for part in at.split(":"))
    _new_drafts(db, factory, user_id, image_ids, plan_slots(count, time(hour, minute), _occupied(db, factory.id)))
    return list_drafts(db, user_id, service_key, advance=False)


def create_one(db: Session, user_id: int, service_key: str, *, scheduled_at: Optional[datetime] = None) -> dict:
    """المرحلة الأولى من المنشور الواحد (الجدولة): مسودة بموعد يختاره الزبون، أو بأقرب موعد مسموح بلا موعد.
    قالبها وصوتها وصورها عشوائية ونصها يُكتب، ثم يعدّلها الزبون ويوافق عليها كأي مسودة."""
    factory, image_ids = _prepare(db, user_id, service_key, 1)
    occupied = _occupied(db, factory.id)
    when = _as_utc(scheduled_at) if scheduled_at is not None else earliest_slot(occupied)
    check_slot(when, occupied)
    draft = _new_drafts(db, factory, user_id, image_ids, [when])[0]
    db.refresh(draft)
    return _one(db, user_id, service_key, draft)


# ───── التقدم بالمسودات (متكرر الأمان) ─────

def advance_captions(db: Session, factory_id: int) -> None:
    """يلتقط نص كل وصف اكتمل، ثم يطلب وصف المسودة التالية إن لم يكن وصف قيد الكتابة."""
    factory = db.get(models.Factory, factory_id)
    writing = db.query(models.PostDraft).filter(
        models.PostDraft.factory_id == factory_id, models.PostDraft.status == WRITING,
    ).order_by(models.PostDraft.scheduled_at, models.PostDraft.id).all()
    busy = finished = False
    for draft in writing:
        job = draft.caption_job
        if job is None:
            continue
        db.refresh(job)
        if job.status == "succeeded" and job.response:
            draft.caption = customer_captions.compose_caption(job.response, factory)
            draft.caption_error, draft.status, finished = None, READY, True
        elif job.status in _ACTIVE_JOB:
            busy = True
        else:
            draft.caption, draft.status, finished = "", READY, True
            draft.caption_error = customer_captions.FRIENDLY_FAILURE
    db.commit()
    if busy:
        return
    nxt = next((draft for draft in writing if draft.status == WRITING and draft.caption_job_id is None), None)
    if nxt is None:
        if finished:
            _notify_ready(db, factory_id)
        return
    try:
        job = ollama_chat.enqueue_description_job(
            db, factory_id, customer_captions.CAPTION_MODEL, hashtags=not factory.hashtags,
        )
    except ServiceError as exc:            # النشاط حُذف مثلاً: لا يُكتب شيء، والزبون يكتب النصوص بنفسه
        db.rollback()
        for draft in writing:
            if draft.status == WRITING and draft.caption_job_id is None:
                draft.caption, draft.status, draft.caption_error = "", READY, str(exc)
        db.commit()
        _notify_ready(db, factory_id)
        return
    nxt.caption_job_id = job.id
    db.commit()


def _notify_ready(db: Session, factory_id: int) -> None:
    """انتهت كتابة نصوص الصفحة كلها: إشعار بعدد المسودات التي تنتظر الموافقة."""
    count = db.query(models.PostDraft).filter(
        models.PostDraft.factory_id == factory_id, models.PostDraft.status == READY,
    ).count()
    if count:
        push_notifications.notify(db, factory_id, push_notifications.drafts_ready(count))


def _claim(db: Session, draft_id: int, before: str, after: str) -> bool:
    """ينقل المسودة من حالة إلى أخرى بجملة UPDATE واحدة: True لمن سبق (عامل آخر لا يكرر العمل)."""
    claimed = db.query(models.PostDraft).filter(
        models.PostDraft.id == draft_id, models.PostDraft.status == before,
    ).update({models.PostDraft.status: after, models.PostDraft.updated_at: utc_now()}, synchronize_session=False)
    db.commit()
    return claimed == 1


def _fail(db: Session, draft: models.PostDraft, message: str) -> None:
    draft.status, draft.error = FAILED, message
    db.commit()
    push_notifications.notify(db, draft.factory_id, push_notifications.draft_failed(draft.id, message))


def _facebook_channel_id(db: Session, factory_id: int) -> Optional[int]:
    ids = post_publisher.active_channel_ids(db, factory_id, platform="facebook")
    return ids[0] if ids else None


def _schedule_post(db: Session, draft: models.PostDraft, video_url: str) -> None:
    """منشور معتمد بموعد المسودة ونصها المعتمد؛ مؤقت النشر ينشره حين يحين."""
    channel_id = _facebook_channel_id(db, draft.factory_id)
    if channel_id is None:
        _fail(db, draft, NO_PAGE_MESSAGE)
        return
    # الصورة الأولى في ترتيب المسودة غلاف الريل: تُحفظ بعد رابط الفيديو، ويرفعها محوّل فيسبوك في thumb
    cover = None
    if draft.image_ids:
        cover = db.query(models.Media.url).filter(
            models.Media.factory_id == draft.factory_id, models.Media.id == draft.image_ids[0],
        ).scalar()
    post = models.Post(
        factory_id=draft.factory_id, media_type="reel", media_urls=[video_url] + ([cover] if cover else []),
        description=draft.caption,
        status=PostStatus.APPROVED, approval=PostApproval.APPROVED, approved_at=draft.approved_at,
        scheduled_at=draft.scheduled_at,
    )
    post.deliveries = [models.PostDelivery(channel_id=channel_id, status=DeliveryStatus.PENDING.value, attempts=0)]
    db.add(post)
    db.flush()
    draft.post_id, draft.status, draft.error = post.id, SCHEDULED, None
    db.commit()


def advance_renders(db: Session, factory_id: Optional[int] = None, now: Optional[datetime] = None) -> None:
    """يطلب فيديو كل مسودة معتمدة اقترب موعدها، وينشئ منشور كل فيديو اكتمل."""
    now = now or utc_now()
    query = db.query(models.PostDraft).filter(models.PostDraft.status.in_((APPROVED, RENDERING)))
    if factory_id is not None:
        query = query.filter(models.PostDraft.factory_id == factory_id)
    for draft in query.order_by(models.PostDraft.scheduled_at, models.PostDraft.id).all():
        if draft.status == APPROVED:
            if draft.scheduled_at > now + RENDER_AHEAD:
                continue
            if draft.scheduled_at < now - LATE_LIMIT:
                _fail(db, draft, LATE_MESSAGE)
                continue
            if not _claim(db, draft.id, APPROVED, RENDERING):
                continue
            db.refresh(draft)
            engine = "remotion" if settings.CUSTOMER_VIDEO_ENGINE == "remotion" else "ffmpeg"
            try:
                job = factory_video_jobs.enqueue_video_job(
                    db, draft.factory_id, source="library", sku=None, template=draft.template,
                    duration=None, audio=draft.audio, image_ids=draft.image_ids or None, engine=engine,
                )
            except ServiceError as exc:
                db.rollback()
                _fail(db, draft, f"تعذّر تجهيز الفيديو: {exc}")
                continue
            draft.video_job_id, draft.error = job.id, None
            db.commit()
            continue
        job = draft.video_job
        if job is None:
            continue
        db.refresh(job)
        if job.status == "failed":
            _fail(db, draft, f"تعذّر تجهيز الفيديو: {job.error or 'سبب غير معروف'}")
        elif job.status == "succeeded" and job.video_url:
            if not _claim(db, draft.id, RENDERING, _FINALIZING):
                continue
            db.refresh(draft)
            if draft.scheduled_at < now - LATE_LIMIT:
                _fail(db, draft, LATE_MESSAGE)
                continue
            try:
                _schedule_post(db, draft, job.video_url)
            except Exception:
                db.rollback()
                logger.exception("Scheduling post for draft %s failed", draft.id)
                _fail(db, draft, "تعذّرت جدولة المنشور؛ اختر موعداً جديداً لإعادة المحاولة")


def advance_all(session_factory: SessionFactory = SessionLocal) -> None:
    """يستدعيها عاملا الفيديو والأوصاف: تتقدم بمسودات كل الصفحات. فشلها يُسجَّل ولا يوقف العامل."""
    with session_factory() as db:
        factory_ids = [row[0] for row in db.query(models.PostDraft.factory_id).filter(
            models.PostDraft.status.in_((WRITING, APPROVED, RENDERING)),
        ).distinct()]
    for factory_id in factory_ids:
        try:
            with session_factory() as db:
                advance_captions(db, factory_id)
                advance_renders(db, factory_id)
        except Exception:
            logger.exception("Advancing post drafts of factory %s failed", factory_id)


# ───── العرض ─────

def _utc(moment: Optional[datetime]) -> Optional[datetime]:
    return moment.replace(tzinfo=timezone.utc) if moment is not None else None


def _view(draft: models.PostDraft, images: dict, page_id: str) -> dict:
    status, error, facebook_url, published_at, video_url = draft.status, draft.error, None, None, None
    if draft.video_job is not None and draft.video_job.status == "succeeded":
        video_url = draft.video_job.video_url
    post = draft.post
    if draft.status == SCHEDULED and post is not None:
        post_view = customer_workspaces._post_view(post, {}, page_id)
        published_at, facebook_url = post_view["published_at"], post_view["facebook_url"]
        if post.status == PostStatus.APPROVED:
            status = SCHEDULED
        elif post.status == PostStatus.PUBLISHED:
            status = "published"
        elif post.status == PostStatus.FAILED:
            status, error = FAILED, post_view["error"]
        elif post.status == PostStatus.PUBLISHING:
            status = "publishing"
        else:                                         # أُرشف من الإدارة
            status, error = FAILED, "أُوقف هذا المنشور"
    elif draft.status == _FINALIZING:
        status = RENDERING
    elif draft.status == CANCELLED:
        error = None
    return {
        "id": draft.id,
        "status": status,
        "template": draft.template,
        "audio": draft.audio,
        "images": [{"id": media_id, "url": images[media_id]} for media_id in draft.image_ids or [] if media_id in images],
        "caption": draft.caption or "",
        "caption_error": draft.caption_error,
        "scheduled_at": _utc(draft.scheduled_at),
        "planned_at": _utc(draft.planned_at or draft.scheduled_at),
        "cancelled_at": _utc(draft.cancelled_at),
        "approved_at": _utc(draft.approved_at),
        "error": error,
        "video_url": video_url,
        "facebook_url": facebook_url,
        "published_at": _utc(published_at),
    }


def list_drafts(db: Session, user_id: int, service_key: str, *, advance: bool = True) -> dict:
    """المسودات والمجدولة والمنشورة في آخر 30 يوماً، والملغاة في آخر 30 يوماً، الأقرب موعداً أولاً."""
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    factory_id = workspace.factory_id
    if advance:                                       # شبكة أمان إن كان العاملان متوقفين لحظةً
        advance_captions(db, factory_id)
        advance_renders(db, factory_id)
    since = utc_now() - PUBLISHED_VISIBLE
    drafts = db.query(models.PostDraft).filter(
        models.PostDraft.factory_id == factory_id,
        or_(
            and_(models.PostDraft.status != CANCELLED, models.PostDraft.scheduled_at >= since),
            and_(models.PostDraft.status == CANCELLED, models.PostDraft.cancelled_at >= since),
        ),
    ).order_by(models.PostDraft.scheduled_at, models.PostDraft.id).all()
    wanted = {media_id for draft in drafts for media_id in draft.image_ids or []}
    images = dict(db.query(models.Media.id, models.Media.url).filter(
        models.Media.factory_id == factory_id, models.Media.id.in_(wanted),
    )) if wanted else {}
    try:
        next_slot = earliest_slot(_occupied(db, factory_id))
    except InvalidInputError:
        next_slot = None
    return {
        "drafts": [_view(draft, images, workspace.page_id) for draft in drafts],
        "rules": rules(),
        "next_slot": _utc(next_slot),
    }


def _draft(db: Session, factory_id: int, draft_id: int) -> models.PostDraft:
    draft = db.query(models.PostDraft).filter(
        models.PostDraft.id == draft_id, models.PostDraft.factory_id == factory_id,
        models.PostDraft.status != CANCELLED,
    ).first()
    if draft is None:
        raise NotFoundError("المسودة غير موجودة؛ حدّث الصفحة")
    return draft


def _one(db: Session, user_id: int, service_key: str, draft: models.PostDraft) -> dict:
    view = list_drafts(db, user_id, service_key, advance=False)
    return next(item for item in view["drafts"] if item["id"] == draft.id)


# ───── تعديل المسودة ─────

def _record_approval(db: Session, draft: models.PostDraft, user_id: int, page_id: str, now: datetime) -> None:
    caption = draft.caption or ""
    db.add(models.PostDraftApproval(
        draft_id=draft.id, user_id=user_id, page_id=page_id, approved_at=now, caption=caption,
        caption_sha256=hashlib.sha256(caption.encode("utf-8")).hexdigest(), template=draft.template,
        audio=draft.audio, image_ids=list(draft.image_ids or []), scheduled_at=draft.scheduled_at,
    ))


def _reschedule(db: Session, draft: models.PostDraft, when: datetime, user_id: int, page_id: str) -> None:
    post = draft.post
    if post is not None and post.status in (PostStatus.PUBLISHING, PostStatus.PUBLISHED):
        raise InvalidInputError("نُشر هذا المنشور أو هو قيد النشر الآن؛ لا يتغير موعده")
    check_slot(when, _occupied(db, draft.factory_id, exclude_draft=draft.id))
    draft.scheduled_at = when
    if draft.status == FAILED:
        if post is not None and post.status == PostStatus.FAILED:        # فشل النشر: يُعاد بموعده الجديد
            post.status, post.error, post.claimed_at = PostStatus.APPROVED, None, None
            draft.status = SCHEDULED
        else:                                                              # فشل التجهيز أو فات الموعد
            draft.status, draft.video_job, draft.post = APPROVED, None, None
        draft.error = None
    if draft.post is not None and draft.status == SCHEDULED:
        draft.post.scheduled_at = when
    if draft.approved_at is not None:                 # موافقة على الموعد الجديد تُسجَّل كذلك
        _record_approval(db, draft, user_id, page_id, utc_now())


CONTENT_FIELDS = ("caption", "template", "audio", "image_ids")


def _check_editable(draft: models.PostDraft) -> None:
    """محتوى المنشور يُعدَّل حتى يبدأ نشره، إلا أثناء كتابة النص أو تجهيز الفيديو (لحظات)."""
    post = draft.post
    if post is not None and post.status in (PostStatus.PUBLISHING, PostStatus.PUBLISHED):
        raise InvalidInputError("نُشر هذا المنشور أو هو قيد النشر الآن؛ لا يتغير محتواه")
    if draft.status == WRITING:
        raise InvalidInputError("انتظر حتى ينتهي الذكاء الاصطناعي من كتابة النص")
    if draft.status in (RENDERING, _FINALIZING):
        raise InvalidInputError("يُجهَّز فيديو هذا المنشور الآن. انتظر دقيقة ثم عدّله.")


def _drop_video(db: Session, draft: models.PostDraft) -> None:
    """يحذف الفيديو المولَّد لمسودة لم يُنشر: محتواها تغيّر فلم يعد يمثلها (ولا يبقى يتيماً في الأرشيف)."""
    job = draft.video_job
    if job is None or not job.video_url:
        return
    media = db.query(models.Media).filter(
        models.Media.factory_id == draft.factory_id, models.Media.url == job.video_url,
    ).first()
    if media is not None:
        media_library.delete_factory_media(db, draft.factory_id, media.id)


def _reopen(db: Session, draft: models.PostDraft) -> None:
    """تعديل محتوى منشور بعد الموافقة: تُلغى الموافقة (سجلها يبقى) ويُوقف منشوره ويُحذف فيديوه،
    فيعود «بانتظار الموافقة». ما يُنشر هو دائماً ما وافق عليه الزبون آخر مرة."""
    if draft.post is not None:
        draft.post.status = PostStatus.ARCHIVED       # الناشر لا يلمس المؤرشف
    _drop_video(db, draft)
    draft.status, draft.approved_at, draft.approved_by, draft.error = READY, None, None, None
    draft.video_job, draft.post = None, None


def _fill_images(db: Session, draft: models.PostDraft, current: List[int], count: int) -> List[int]:
    """صور القالب الجديد: الحالية حتى عدده، وما نقص يُملأ بصور عشوائية من المكتبة غير مستعملة في المنشور."""
    kept = list(current)[:count]
    rest = [media_id for media_id in _library_images(db, draft.factory_id) if media_id not in kept]
    if len(kept) + len(rest) < count:
        raise InvalidInputError(
            f"هذا القالب يحتاج {images_label(count)}، وفي مكتبتك {images_label(len(kept) + len(rest))}. "
            "ارفع صوراً أكثر أو اختر قالباً أقصر"
        )
    return kept + random.sample(rest, count - len(kept))


def _content(db: Session, draft: models.PostDraft, changes: dict) -> dict:
    """القيم الجديدة للمحتوى بعد التحقق منها. الصور بعدد صور القالب تماماً؛ تغيير القالب وحده يقصّها إلى عدده
    أو يكملها بصور عشوائية من المكتبة."""
    template = video_maker.get_video_template(changes.get("template") or draft.template)
    result: dict = {}
    if "template" in changes and changes["template"] is not None:
        result["template"] = template.id
    if "image_ids" in changes and changes["image_ids"] is not None:
        ids = list(changes["image_ids"])
        if len(set(ids)) != len(ids):
            raise InvalidInputError("الصورة الواحدة تُختار مرة واحدة")
        if len(ids) != template.max_images:
            raise InvalidInputError(f"هذا القالب يأخذ {images_label(template.max_images)} بالضبط")
        found = db.query(models.Media.id).filter(
            models.Media.factory_id == draft.factory_id, models.Media.media_type == "image",
            models.Media.id.in_(ids),
        ).count()
        if found != len(ids):
            raise NotFoundError("بعض الصور المختارة غير موجودة في مكتبتك؛ حدّث الصفحة واختر من جديد")
        result["image_ids"] = ids
    elif "template" in result:
        result["image_ids"] = _fill_images(db, draft, draft.image_ids or [], template.max_images)
    if "audio" in changes:
        audio = changes["audio"] or None
        if audio is not None and audio not in video_maker.list_audio_files():
            raise InvalidInputError("ملف الصوت غير متاح؛ اختر صوتاً من القائمة")
        result["audio"] = audio
    if changes.get("caption") is not None:
        result["caption"] = changes["caption"].strip()
    return {key: value for key, value in result.items() if getattr(draft, key) != value}


def update_draft(
    db: Session, user_id: int, service_key: str, draft_id: int, changes: dict,
) -> dict:
    """المحتوى (caption, template, audio, image_ids): حتى يبدأ النشر؛ تعديله بعد الموافقة يعيد المنشور
    «بانتظار الموافقة». scheduled_at (أو earliest = أقرب موعد مسموح): قبل النشر، بقيود الجدولة، ولا يُلغي الموافقة."""
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    draft = _draft(db, workspace.factory_id, draft_id)
    if any(key in changes for key in CONTENT_FIELDS):
        _check_editable(draft)
        content = _content(db, draft, changes)
        if content and draft.status != READY:
            _reopen(db, draft)
        for key, value in content.items():
            setattr(draft, key, value)
        if "caption" in content:
            draft.caption_error = None
    if changes.get("earliest"):
        when = earliest_slot(_occupied(db, draft.factory_id, exclude_draft=draft.id))
        _reschedule(db, draft, when, user_id, workspace.page_id)
    elif changes.get("scheduled_at") is not None:
        _reschedule(db, draft, _as_utc(changes["scheduled_at"]), user_id, workspace.page_id)
    db.commit()
    advance_renders(db, workspace.factory_id)
    db.refresh(draft)
    return _one(db, user_id, service_key, draft)


def rewrite_caption(db: Session, user_id: int, service_key: str, draft_id: int) -> dict:
    """يطلب نصاً جديداً من Gemini. لمنشور وافق عليه: يعود «بانتظار الموافقة» كأي تعديل للمحتوى."""
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    draft = _draft(db, workspace.factory_id, draft_id)
    _check_editable(draft)
    if draft.status != READY:
        _reopen(db, draft)
    draft.status, draft.caption_job_id, draft.caption_error = WRITING, None, None
    db.commit()
    advance_captions(db, workspace.factory_id)
    return _one(db, user_id, service_key, draft)


def delete_draft(db: Session, user_id: int, service_key: str, draft_id: int) -> None:
    """تُلغى المسودة (قبل الموافقة أو بعدها وقبل النشر) ويُوقف منشورها إن وُجد. لا يُحذف الصف: تظهر في
    «الملغاة» 30 يوماً ويسترجعها الزبون. المنشور فعلاً لا يُلغى من هنا."""
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    draft = _draft(db, workspace.factory_id, draft_id)
    post = draft.post
    if post is not None and post.status in (PostStatus.PUBLISHING, PostStatus.PUBLISHED):
        raise InvalidInputError("نُشر هذا المنشور أو هو قيد النشر الآن. احذفه من صفحتك على فيسبوك إن أردت.")
    if post is not None:
        post.status = PostStatus.ARCHIVED          # الناشر لا يلمس المؤرشف
    draft.status, draft.cancelled_at = CANCELLED, utc_now()
    db.commit()


def restore_draft(db: Session, user_id: int, service_key: str, draft_id: int) -> dict:
    """يعيد مسودة ملغاة إلى «بانتظار المراجعة»، ولو كانت موافقاً عليها: تُراجع ويُوافق عليها من جديد.
    إن لم يعد موعدها صالحاً (فات، أو أخذ يومَه منشور آخر) تُنقل إلى أول يوم فارغ في الساعة نفسها."""
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    factory_id = workspace.factory_id
    draft = db.query(models.PostDraft).filter(
        models.PostDraft.id == draft_id, models.PostDraft.factory_id == factory_id,
        models.PostDraft.status == CANCELLED,
    ).first()
    if draft is None:
        raise NotFoundError("المسودة الملغاة غير موجودة؛ حدّث الصفحة")
    if _count_pending(db, factory_id) >= MAX_OPEN:
        raise InvalidInputError(f"لديك {MAX_OPEN} مسودة بانتظار المراجعة. راجعها أو احذف بعضها أولاً.")
    occupied = _occupied(db, factory_id)
    try:
        check_slot(draft.scheduled_at, occupied)
    except InvalidInputError:
        hour = (draft.planned_at or draft.scheduled_at).replace(tzinfo=timezone.utc).astimezone(tz()).time()
        draft.scheduled_at = plan_slots(1, hour, occupied)[0]
    needs_text = not (draft.caption or "").strip() and draft.caption_error is None
    draft.status = WRITING if needs_text else READY
    if needs_text:
        draft.caption_job_id = None
    draft.approved_at = draft.approved_by = draft.cancelled_at = draft.error = None
    _drop_video(db, draft)
    draft.video_job, draft.post = None, None
    db.commit()
    advance_captions(db, factory_id)
    db.refresh(draft)
    return _one(db, user_id, service_key, draft)


# ───── الموافقة ─────

def approve(db: Session, user_id: int, service_key: str, ids: List[int]) -> dict:
    """يوافق على المسودات التي راجعها الزبون كما ظهرت له، ويسجّل الموافقة (من، ومتى، وعلى أي نسخة)."""
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    factory_id = workspace.factory_id
    channel = facebook_services.connected_channel(db, user_id, service_key)
    if not channel.is_active or not channel.page_access_token:
        raise InvalidInputError("صفحتك تحتاج إعادة ربط قبل جدولة النشر عليها. اضغط «تغيير الصفحة» وأعد ربطها.")
    ids = list(dict.fromkeys(ids))
    drafts = db.query(models.PostDraft).filter(
        models.PostDraft.factory_id == factory_id, models.PostDraft.id.in_(ids),
    ).all()
    if len(drafts) != len(ids) or any(draft.status != READY for draft in drafts):
        raise InvalidInputError("تغيّرت بعض المسودات منذ عرضها (حُذفت أو ما زال نصها يُكتب). حدّث الصفحة وراجعها.")
    now = utc_now()
    for draft in sorted(drafts, key=lambda item: item.scheduled_at):
        when = draft.scheduled_at.replace(tzinfo=timezone.utc).astimezone(tz()).strftime("%Y-%m-%d %H:%M")
        if not (draft.caption or "").strip():
            raise InvalidInputError(f"منشور {when} بلا نص. اكتب نصه أو احذفه ثم وافق.")
        needed = video_maker.get_video_template(draft.template).max_images
        if len(draft.image_ids or []) != needed:
            raise InvalidInputError(f"منشور {when} قالبه يأخذ {images_label(needed)}. افتحه وأكمل صوره ثم وافق.")
        if draft.scheduled_at < now + MIN_LEAD:
            raise InvalidInputError(f"موعد منشور {when} فات أو قريب جداً. غيّره ثم وافق.")
    for draft in drafts:
        draft.status, draft.approved_at, draft.approved_by, draft.error = APPROVED, now, user_id, None
        _record_approval(db, draft, user_id, workspace.page_id, now)
    db.commit()
    advance_renders(db, factory_id)
    return list_drafts(db, user_id, service_key, advance=False)
