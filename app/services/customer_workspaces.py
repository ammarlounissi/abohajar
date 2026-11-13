"""مساحة عمل الزبون: مكتبة وسائط الصفحة التي ربطها بخدمة. لا تعرف شيئاً عن HTTP.

القاعدة: كل صفحة مربوطة = مصنع خاص بها (models.Factory, kind=customer). الوسائط والفيديوهات والمنشورات
تتبع ذلك المصنع، لا المستخدم ولا صف القناة، فيُعاد استعمال مسار المصانع كله كما هو
(media_library، factory_videos، الناشر).

المصنع يُستخرج دائماً من (المستخدم، الصفحة المربوطة بالخدمة). لا يُقبل رقم مصنع من الزبون أبداً.

اتفاقية الحفظ: ensure_workspace لا تنفّذ commit (تُستدعى ضمن ربط الصفحة)؛ بقية الدوال تحفظ بنفسها."""
import io
import random
from datetime import timedelta, timezone
from pathlib import Path
from typing import BinaryIO, List, Optional

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.core.time import utc_now
from app.models import DeliveryStatus, FactoryKind, PostApproval, PostStatus
from app.services import (
    facebook_services, factory_video_jobs, factory_videos, media_library, post_publisher, video_maker,
)
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

# حدود رفع الزبون. الصورة الواحدة بحد ما يقبله مولّد الفيديو (factory_videos.MAX_LIBRARY_IMAGE_*).
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGES = 200
# الفيديو المولَّد يُحفظ في المكتبة نفسها (حتى 100 ميغابايت للواحد)، فلعددها سقف: يحذف الزبون ليولّد غيرها.
MAX_VIDEOS = 20
_ACTIVE_JOB_STATUSES = ("queued", "running")
# صيغة المحتوى كما يتعرّف عليها Pillow -> (امتداد الملف المحفوظ، نوع المحتوى). الصيغة تُعرف من المحتوى لا
# من اسم الملف: الهواتف ترسل أسماء بلا امتداد (منتقي Android) أو بامتداد لا يطابق (صورة PNG محفوظة باسم .jpg).
# MPO = JPEG متعدد الصور تحفظه كاميرات Android بوضع البورتريه أو الحركة باسم .jpg؛ يُحفظ JPEG عادياً بصورته الأولى.
IMAGE_FORMATS = {
    "JPEG": (".jpg", "image/jpeg"), "MPO": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"), "WEBP": (".webp", "image/webp"),
}
IMAGE_EXTENSIONS = (".jpeg", ".jpg", ".png", ".webp")      # لمنتقي الملفات في الواجهة
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
_HEIF_BRANDS = (b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1", b"avif")
_EXIF_ORIENTATION, _EXIF_GPS = 0x0112, 0x8825


def _page_id(channel: models.SocialChannel) -> str:
    return channel.page_id or channel.remote_id


def _factory_name(channel: models.SocialChannel) -> str:
    name = (channel.page_name or channel.name or "").strip()[:100]
    return name if len(name) >= 2 else f"صفحة {_page_id(channel)}"[:100]


def ensure_workspace(db: Session, *, user_id: int, channel: models.SocialChannel) -> models.CustomerWorkspace:
    """مساحة هذا المستخدم لهذه الصفحة، وتُنشأ مع مصنعها عند أول مرة. تجعل القناة تابعة لمصنع المساحة
    كي ينشر الناشر عليها. لا commit.

    - صفحة أسندتها الإدارة إلى مصنع مورّد: تُعتمد مساحةً كما هي (الصفحة صفحته).
    - صفحة بقي صفها مشيراً إلى مساحة زبون سابق (صف مفكوك أُعيد ربطه): لا تُورَّث، يُنشأ مصنع جديد."""
    page_id = _page_id(channel)
    workspace = (
        db.query(models.CustomerWorkspace)
        .filter(models.CustomerWorkspace.user_id == user_id, models.CustomerWorkspace.page_id == page_id)
        .first()
    )
    if workspace is None:
        factory = channel.factory
        if factory is None or factory.kind != FactoryKind.SUPPLIER.value:
            factory = models.Factory(name=_factory_name(channel), kind=FactoryKind.CUSTOMER.value)
            db.add(factory)
        workspace = models.CustomerWorkspace(user_id=user_id, page_id=page_id, factory=factory)
        db.add(workspace)
    channel.factory = workspace.factory
    db.flush()
    return workspace


def current_workspace(db: Session, user_id: int, service_key: str) -> models.CustomerWorkspace:
    """مساحة الصفحة المربوطة بهذه الخدمة للمستخدم. NotFoundError لخدمة مجهولة، ConflictError إن لم يربط صفحة."""
    service = facebook_services.get_service(service_key)
    channel = facebook_services.connected_channel(db, user_id, service.key)
    if channel is None:
        raise ConflictError("اربط صفحتك بالخدمة أولاً")
    workspace = ensure_workspace(db, user_id=user_id, channel=channel)
    db.commit()
    return workspace


def _count(db: Session, factory_id: int, media_type: str) -> int:
    return (
        db.query(models.Media)
        .filter(models.Media.factory_id == factory_id, models.Media.media_type == media_type)
        .count()
    )


def describe(db: Session, user_id: int, service_key: str) -> dict:
    """ملخص المساحة كما تعرضه لوحة الزبون: الصفحة، عدد الوسائط، وحدود الرفع."""
    workspace = current_workspace(db, user_id, service_key)
    channel = facebook_services.connected_channel(db, user_id, service_key)
    return {
        "service": service_key,
        "page_id": workspace.page_id,
        "page_name": channel.page_name or channel.name,
        "images_count": _count(db, workspace.factory_id, "image"),
        "videos_count": _count(db, workspace.factory_id, "video"),
        "max_images": MAX_IMAGES,
        "max_image_bytes": MAX_IMAGE_BYTES,
        "image_extensions": list(IMAGE_EXTENSIONS),
        "image_types": list(IMAGE_TYPES),
    }


def list_media(
    db: Session, user_id: int, service_key: str, *, media_type: Optional[str] = None,
    skip: int = 0, limit: int = 100,
) -> List[models.Media]:
    workspace = current_workspace(db, user_id, service_key)
    return media_library.list_factory_media(
        db, workspace.factory_id, media_type=media_type, skip=skip, limit=limit,
    )


def _is_heif(data: bytes, extension: str, content_type: Optional[str]) -> bool:
    """صور HEIC/HEIF (آيفون وبعض هواتف Samsung): لا يقرؤها Pillow دون مكتبة إضافية."""
    if extension in (".heic", ".heif", ".avif") or (content_type or "").lower() in ("image/heic", "image/heif", "image/avif"):
        return True
    return data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS


def _needs_rewrite(image: Image.Image) -> bool:
    """صورة JPEG فيها اتجاه EXIF أو موقع GPS، أو MPO: تُعاد كتابتها. غير ذلك يُحفظ كما رُفع."""
    if image.format == "MPO":
        return True
    if image.format != "JPEG":
        return False
    exif = image.getexif()
    return exif.get(_EXIF_ORIENTATION, 1) != 1 or _EXIF_GPS in exif


def _rewrite_jpeg(image: Image.Image) -> bytes:
    """الصورة الأولى مُدارة كما تعرضها الكاميرا، بلا بيانات EXIF (لا اتجاه ولا موقع: الرابط عام)."""
    image.seek(0)
    upright = ImageOps.exif_transpose(image)
    if upright.mode not in ("RGB", "L"):
        upright = upright.convert("RGB")
    buffer = io.BytesIO()
    upright.save(buffer, "JPEG", quality=92, icc_profile=image.info.get("icc_profile"))
    return buffer.getvalue()


def _read_image(file: BinaryIO, extension: str, content_type: Optional[str] = None) -> tuple[bytes, str, str]:
    """يقرأ الملف كله (بسقف الحجم) ويتحقق أن محتواه صورة مقبولة. يرجع (البيانات، الامتداد، نوع المحتوى)
    من المحتوى نفسه، بعد تصحيح اتجاه صور الكاميرا وإزالة موقعها."""
    data = file.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise InvalidInputError("لا يمكن رفع ملف فارغ")
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidInputError("حجم الصورة يتجاوز الحد الأقصى (20 ميغابايت)")
    if _is_heif(data, extension, content_type):
        raise InvalidInputError(
            "صور HEIC غير مقبولة بعد. حوّل الصورة إلى JPG، أو اضبط كاميرا الهاتف على الصيغة «الأكثر توافقاً»."
        )
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format
            pixels = image.width * image.height
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidInputError("الملف ليس صورة صالحة") from exc
    if image_format not in IMAGE_FORMATS:
        raise InvalidInputError("الصيغ المقبولة: JPG وPNG وWebP")
    if pixels > MAX_IMAGE_PIXELS:
        raise InvalidInputError("أبعاد الصورة أكبر من الحد المسموح")
    saved_extension, saved_type = IMAGE_FORMATS[image_format]
    try:
        with Image.open(io.BytesIO(data)) as image:     # verify() يُغلق الصورة، فتُفتح من جديد
            if _needs_rewrite(image):
                data = _rewrite_jpeg(image)
    except (OSError, SyntaxError, ValueError) as exc:
        raise InvalidInputError("الملف ليس صورة صالحة") from exc
    return data, saved_extension, saved_type


def upload_image(
    db: Session, user_id: int, service_key: str, *, file: BinaryIO, filename: Optional[str],
    content_type: Optional[str], description: Optional[str],
) -> models.Media:
    """يرفع صورة إلى مكتبة صفحة الزبون. أشد من رفع الإدارة: صور فقط، يُفحص محتواها، وبسقف للعدد.
    اسم الملف ونوعه كما أرسلهما المتصفح لا يُعتمد عليهما (الهواتف تخطئ فيهما)؛ الصيغة من المحتوى."""
    workspace = current_workspace(db, user_id, service_key)
    extension = Path(filename or "").suffix.lower()
    data, saved_extension, saved_type = _read_image(file, extension, content_type)
    if _count(db, workspace.factory_id, "image") >= MAX_IMAGES:
        raise InvalidInputError(f"بلغت الحد الأقصى لعدد الصور ({MAX_IMAGES}). احذف صوراً لترفع غيرها.")
    return media_library.upload_factory_media(
        db, workspace.factory_id, file=io.BytesIO(data), filename=f"image{saved_extension}",
        content_type=saved_type, description=description,
    )


def delete_media(db: Session, user_id: int, service_key: str, media_id: int) -> None:
    """يحذف وسيطاً من مكتبة صفحة الزبون فقط (NotFoundError لما ليس فيها).
    فيديو قيد النشر لا يُحذف: فيسبوك ينزّله من رابطه أثناء النشر."""
    workspace = current_workspace(db, user_id, service_key)
    item = db.query(models.Media).filter(
        models.Media.id == media_id, models.Media.factory_id == workspace.factory_id,
    ).first()
    if item is not None and item.media_type == "video":
        post = _latest_post(_reel_posts(db, workspace.factory_id), item.url)
        if post is not None and post.status in _IN_FLIGHT:
            raise InvalidInputError("هذا الفيديو قيد النشر الآن. انتظر انتهاء النشر ثم احذفه.")
    media_library.delete_factory_media(db, workspace.factory_id, media_id)


# ───── توليد الفيديو من صور المكتبة (طابور factory_video_jobs، والمحرك CUSTOMER_VIDEO_ENGINE) ─────

def _active_job(db: Session, factory_id: int) -> Optional[models.FactoryVideoJob]:
    return (
        db.query(models.FactoryVideoJob)
        .filter(
            models.FactoryVideoJob.factory_id == factory_id,
            models.FactoryVideoJob.status.in_(_ACTIVE_JOB_STATUSES),
        )
        .first()
    )


def video_options(db: Session, user_id: int, service_key: str) -> dict:
    """ما يختار منه الزبون قبل التوليد: القوالب والأصوات، مع عدد صوره وفيديوهاته وسقفها."""
    workspace = current_workspace(db, user_id, service_key)
    options = factory_videos.get_video_options(db, workspace.factory_id)
    return {
        # مع إعدادات الحركة والانتقال التي يرسم بها مكوّن المعاينة (ProductReel) القالب
        "templates": [
            {**item, **video_maker.template_spec(video_maker.get_video_template(item["id"]))}
            for item in options["templates"]
        ],
        "default_template": options["default_template"],
        "audio_files": options["audio_files"],
        "images_count": options["library_images_count"],
        "videos_count": _count(db, workspace.factory_id, "video"),
        "max_videos": MAX_VIDEOS,
    }


def _check_image_ids(db: Session, factory_id: int, image_ids: List[int], max_images: int) -> None:
    """الصور المختارة: من صور مكتبة هذه الصفحة، بلا تكرار، وبحد القالب."""
    if len(set(image_ids)) != len(image_ids):
        raise InvalidInputError("الصورة الواحدة تُختار مرة واحدة")
    if len(image_ids) > max_images:
        raise InvalidInputError(f"هذا القالب يأخذ {max_images} صورة على الأكثر")
    found = (
        db.query(models.Media.id)
        .filter(
            models.Media.factory_id == factory_id,
            models.Media.media_type == "image",
            models.Media.id.in_(image_ids),
        )
        .count()
    )
    if found != len(image_ids):
        raise NotFoundError("بعض الصور المختارة غير موجودة في مكتبتك؛ حدّث الصفحة واختر من جديد")


def request_video(
    db: Session, user_id: int, service_key: str, *, template: Optional[str], audio: Optional[str],
    image_ids: Optional[List[int]] = None,
) -> models.FactoryVideoJob:
    """يضع في الطابور فيديو من صور مكتبة الصفحة. مهمة واحدة جارية لكل صفحة، وبسقف لعدد الفيديوهات.
    بلا صوت محدد يُختار صوت عشوائي من المتاح. image_ids: الصور بترتيب المعاينة (بدونها: اختيار عشوائي).
    المحرك من CUSTOMER_VIDEO_ENGINE ويُحفظ مع المهمة."""
    workspace = current_workspace(db, user_id, service_key)
    factory_id = workspace.factory_id
    selected_template = video_maker.get_video_template(template or video_maker.DEFAULT_FACTORY_TEMPLATE)
    if _active_job(db, factory_id) is not None:
        raise InvalidInputError("يوجد فيديو قيد التوليد لهذه الصفحة. انتظر اكتماله ثم اطلب غيره.")
    if _count(db, factory_id, "image") == 0:
        raise InvalidInputError("ارفع صوراً إلى المكتبة أولاً ليُبنى منها الفيديو")
    if _count(db, factory_id, "video") >= MAX_VIDEOS:
        raise InvalidInputError(f"بلغت الحد الأقصى لعدد الفيديوهات ({MAX_VIDEOS}). احذف فيديو لتولّد غيره.")
    if image_ids:
        _check_image_ids(db, factory_id, image_ids, selected_template.max_images)
    if audio is None:
        audio_files = video_maker.list_audio_files()
        audio = random.choice(audio_files) if audio_files else None
    engine = "remotion" if settings.CUSTOMER_VIDEO_ENGINE == "remotion" else "ffmpeg"
    return factory_video_jobs.enqueue_video_job(
        db, factory_id, source="library", sku=None,
        template=selected_template.id, duration=None, audio=audio,
        image_ids=image_ids or None, engine=engine,
    )


def audio_file(db: Session, user_id: int, service_key: str, name: str) -> Path:
    """ملف صوت من المتاح للتوليد، لمعاينة الفيديو في اللوحة. لصاحب صفحة مربوطة فقط."""
    current_workspace(db, user_id, service_key)
    return video_maker.resolve_audio(name)


def _job_view(job: models.FactoryVideoJob, media_ids: dict) -> dict:
    """المهمة كما يراها الزبون، ومعها رقم الفيديو الناتج في المكتبة (لينشره مباشرة بعد التوليد)."""
    return {
        "id": job.id, "status": job.status, "template": job.template, "duration": job.duration,
        "audio": job.audio, "video_url": job.video_url, "images_used": job.images_used, "error": job.error,
        "created_at": job.created_at, "completed_at": job.completed_at,
        "media_id": media_ids.get(job.video_url) if job.video_url else None,
    }


def list_video_jobs(db: Session, user_id: int, service_key: str, *, limit: int = 10) -> List[dict]:
    """آخر مهام التوليد لصفحة الزبون، الأحدث أولاً."""
    workspace = current_workspace(db, user_id, service_key)
    jobs = (
        db.query(models.FactoryVideoJob)
        .filter(models.FactoryVideoJob.factory_id == workspace.factory_id)
        .order_by(models.FactoryVideoJob.id.desc())
        .limit(limit)
        .all()
    )
    media_ids = _media_ids(db, workspace.factory_id)
    return [_job_view(job, media_ids) for job in jobs]


def get_video_job(db: Session, user_id: int, service_key: str, job_id: int) -> dict:
    """مهمة من مهام صفحة الزبون فقط (NotFoundError لغيرها)."""
    workspace = current_workspace(db, user_id, service_key)
    job = factory_video_jobs.get_video_job(db, workspace.factory_id, job_id)
    return _job_view(job, _media_ids(db, workspace.factory_id))


# ───── نشر الفيديو على الصفحة المربوطة (الناشر وpost_deliveries كما هما) ─────

MAX_CAPTION = 2200
_IN_FLIGHT = (PostStatus.APPROVED, PostStatus.PUBLISHING)
_SHOWN = _IN_FLIGHT + (PostStatus.PUBLISHED, PostStatus.FAILED)
_TOKEN_MARKS = ("(#190", "(#463", "(#467", "page_access_token_unavailable", "config_missing")
_PERMISSION_MARKS = ("(#200", "(#10)", "(#10/")


def _reel_posts(db: Session, factory_id: int) -> List[models.Post]:
    """منشورات الريل لمصنع الصفحة، الأحدث أولاً."""
    return (
        db.query(models.Post)
        .filter(models.Post.factory_id == factory_id, models.Post.media_type == "reel")
        .order_by(models.Post.id.desc())
        .limit(500)
        .all()
    )


def _post_url(post: models.Post) -> Optional[str]:
    urls = list(post.media_urls or [])
    return urls[0] if urls else None


def _latest_post(posts: List[models.Post], url: str) -> Optional[models.Post]:
    return next((post for post in posts if _post_url(post) == url), None)


def _friendly_error(raw: Optional[str]) -> str:
    """رسالة للزبون من خطأ النشر التقني (رموز Graph أو رموزنا القصيرة)."""
    raw = raw or ""
    if any(mark in raw for mark in _TOKEN_MARKS):
        return "انتهت صلاحية ربط الصفحة أو سُحب الإذن. اضغط «تغيير الصفحة» وأعد ربطها ثم أعد المحاولة."
    if any(mark in raw for mark in _PERMISSION_MARKS):
        return "لا يملك التطبيق إذن النشر على هذه الصفحة. أعد ربطها وامنح الأذونات كلها ثم أعد المحاولة."
    if "channel_inactive" in raw:
        return "الصفحة متوقفة. أعد ربطها ثم أعد المحاولة."
    return "تعذّر النشر على Facebook. أعد المحاولة بعد قليل."


def _post_view(post: models.Post, media_ids: dict, page_id: str) -> dict:
    """ما يراه الزبون من منشور فيديو. unknown: بقي قيد النشر أكثر من المهلة فلا نعرف هل نُشر."""
    delivery = post.deliveries[0] if post.deliveries else None
    status = post.status.value
    if post.status in _IN_FLIGHT:
        stale = post.status == PostStatus.PUBLISHING and (
            post.claimed_at is None
            or utc_now() - post.claimed_at > timedelta(minutes=settings.PUBLISH_STALE_MINUTES)
        )
        status = "unknown" if stale else "publishing"
        if post.status == PostStatus.APPROVED and post.scheduled_at and post.scheduled_at > utc_now():
            status = "scheduled"             # منشور مجدول (النشر الجماعي) ينتظر موعده
    failed = post.status == PostStatus.FAILED
    raw_error = ((delivery.error if delivery else None) or post.error) if failed else None
    remote_id = delivery.remote_id if delivery and delivery.status == DeliveryStatus.PUBLISHED.value else None
    url = _post_url(post)
    return {
        "id": post.id,
        "media_id": media_ids.get(url),
        "video_url": url,
        "caption": post.description or "",
        "status": status,
        "error": _friendly_error(raw_error) if failed else None,
        "error_detail": (raw_error or None) and raw_error[:300],
        "published_at": post.published_at,
        "scheduled_at": post.scheduled_at.replace(tzinfo=timezone.utc) if post.scheduled_at else None,
        "facebook_url": f"https://www.facebook.com/{page_id}/videos/{remote_id}" if remote_id else None,
    }


def _media_ids(db: Session, factory_id: int) -> dict:
    rows = db.query(models.Media.url, models.Media.id).filter(
        models.Media.factory_id == factory_id, models.Media.media_type == "video",
    )
    return {url: media_id for url, media_id in rows}


def list_video_posts(db: Session, user_id: int, service_key: str) -> List[dict]:
    """حالة نشر كل فيديو على الصفحة: آخر منشور لكل فيديو، الأحدث أولاً."""
    workspace = current_workspace(db, user_id, service_key)
    media_ids = _media_ids(db, workspace.factory_id)
    seen, views = set(), []
    for post in _reel_posts(db, workspace.factory_id):
        url = _post_url(post)
        if url is None or url in seen or post.status not in _SHOWN:
            continue
        seen.add(url)
        views.append(_post_view(post, media_ids, workspace.page_id))
    return views


VIDEO_STATES = ("ready", "scheduled", "publishing", "published", "failed", "unknown")


def video_library(db: Session, user_id: int, service_key: str) -> dict:
    """فيديوهات مكتبة الصفحة، الأحدث أولاً، كل منها بحالته (ready أو حالة آخر منشور له) وبما صنعه من قالب،
    مع عدد كل حالة والتوليد الجاري. هي المرجع لصفحة «مكتبة الفيديو» وفلاترها، ولقائمة ما يُنشر."""
    workspace = current_workspace(db, user_id, service_key)
    factory_id = workspace.factory_id
    videos = (
        db.query(models.Media)
        .filter(models.Media.factory_id == factory_id, models.Media.media_type == "video")
        .order_by(models.Media.id.desc())
        .limit(MAX_VIDEOS * 5)          # فوق السقف احتياطاً لفيديوهات الإدارة القديمة
        .all()
    )
    media_ids = {video.url: video.id for video in videos}
    latest: dict = {}
    for post in _reel_posts(db, factory_id):
        url = _post_url(post)
        if url in media_ids and url not in latest and post.status in _SHOWN:
            latest[url] = _post_view(post, media_ids, workspace.page_id)
    jobs = {
        job.video_url: job
        for job in db.query(models.FactoryVideoJob).filter(
            models.FactoryVideoJob.factory_id == factory_id,
            models.FactoryVideoJob.video_url.in_(list(media_ids)),
        )
    } if media_ids else {}
    names: dict = {}

    def template_name(template_id: str) -> str:
        if template_id not in names:
            try:
                names[template_id] = video_maker.get_video_template(template_id).name
            except Exception:       # قالب حُذف من القائمة بعد التوليد
                names[template_id] = template_id
        return names[template_id]

    items = []
    for video in videos:
        post, job = latest.get(video.url), jobs.get(video.url)
        items.append({
            "id": video.id,
            "url": video.url,
            "status": post["status"] if post else "ready",
            "template": template_name(job.template) if job else None,
            "duration": job.duration if job else None,
            "created_at": (job.completed_at or job.created_at).replace(tzinfo=timezone.utc) if job else None,
            "post": post,
        })
    counts = {state: 0 for state in VIDEO_STATES}
    for item in items:
        counts[item["status"]] += 1
    counts["all"] = len(items)
    active = _active_job(db, factory_id)
    return {
        "videos": items,
        "counts": counts,
        "generating": _job_view(active, media_ids) if active else None,
        "max_videos": MAX_VIDEOS,
    }


def publish_video(
    db: Session, user_id: int, service_key: str, media_id: int, *, caption: Optional[str],
) -> dict:
    """يحجز نشر فيديو من مكتبة الصفحة على الصفحة المربوطة فوراً ويرجع حالته (publishing).
    المستدعي يشغّل النشر نفسه بعد الرد: post_publisher.publish_claimed_in_background(id).
    الفيديو يُنشر مرة واحدة؛ النشر الفاشل تُعاد محاولته على المنشور نفسه."""
    workspace = current_workspace(db, user_id, service_key)
    factory_id = workspace.factory_id
    channel = facebook_services.connected_channel(db, user_id, service_key)
    video = db.query(models.Media).filter(
        models.Media.id == media_id, models.Media.factory_id == factory_id,
        models.Media.media_type == "video",
    ).first()
    if video is None:
        raise NotFoundError("الفيديو غير موجود في مكتبة صفحتك")
    if not channel.is_active or not channel.page_access_token:
        raise InvalidInputError("صفحتك تحتاج إعادة ربط قبل النشر عليها. اضغط «تغيير الصفحة» وأعد ربطها.")
    caption = (caption or "").strip()
    if len(caption) > MAX_CAPTION:
        raise InvalidInputError(f"النص أطول من الحد المسموح ({MAX_CAPTION} حرفاً)")

    existing = _latest_post(_reel_posts(db, factory_id), video.url)
    if existing is not None and existing.status == PostStatus.PUBLISHED:
        raise InvalidInputError("نُشر هذا الفيديو على صفحتك من قبل")
    if existing is not None and existing.status in _IN_FLIGHT:
        raise InvalidInputError("هذا الفيديو قيد النشر الآن")
    if existing is not None and existing.status == PostStatus.FAILED:
        existing.description = caption
        post_publisher.set_channels(db, existing, [channel.id])     # يحفظ (commit)
        post = post_publisher.claim_post(db, existing.id)
    else:
        now = utc_now()
        # يُنشأ محجوزاً (publishing) مباشرة: لو أُنشئ approved لالتقطه مؤقت النشر الدوري قبل نشرنا الفوري
        post = models.Post(
            factory_id=factory_id, media_type="reel", media_urls=[video.url], description=caption,
            status=PostStatus.PUBLISHING, approval=PostApproval.APPROVED, approved_at=now, claimed_at=now,
        )
        post.deliveries = [
            models.PostDelivery(channel_id=channel.id, status=DeliveryStatus.PENDING.value, attempts=0),
        ]
        db.add(post)
        db.commit()
        db.refresh(post)
    return _post_view(post, {video.url: video.id}, workspace.page_id)
