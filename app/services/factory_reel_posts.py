"""إنشاء منشور ريل معتمد لمصنع من ناتج مهمتين منتهيتين: مهمة فيديو ومهمة وصف (Ollama).

المنشور يُنشأ approved جاهزاً لمؤقت النشر، بلا موعد (scheduled_at=None) فيلتقطه المؤقت فور إنشائه، وقنواته
هي كل قنوات المصنع المفعّلة. لا شبكة هنا ولا HTTP: دالة واحدة تنفّذ commit بنفسها (اتفاقية الخدمات)."""
import json
import re
from typing import Any, List

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


_TEXT_KEYS = ("description", "caption", "text", "content", "response", "post", "message", "وصف", "النص")
_TAG_KEYS = ("hashtags", "tags", "هاشتاقات")
_FENCE = re.compile(r"^\s*```[\w-]*\s*|\s*```\s*$")


def _parse_json(raw: str) -> Any:
    """يفك JSON حتى لو أحاط به سور markdown أو كلام قبله وبعده؛ وإلا يرجع None."""
    text = _FENCE.sub("", raw.strip()).strip()
    for candidate in (text, *re.findall(r"\{.*\}|\[.*\]", text, re.S)):
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    return None


def _strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for v in value.values() for item in _strings(v)]
    if isinstance(value, list):
        return [item for v in value for item in _strings(v)]
    return []


def _clean_text(text: str) -> str:
    """يحذف رموز التنسيق الزائدة (markdown وأقواس وعلامات اقتباس محيطة) ويُبقي الإيموجي والوسوم."""
    text = text.replace("\\n", "\n").replace("\r", "")
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)         # عناوين markdown (وليس الوسوم #كلمة)
    text = re.sub(r"(\*\*|__|~~|`)", "", text)
    text = re.sub(r"^\s*[*\-•]\s+", "", text, flags=re.M)             # رموز القوائم
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip().strip("{}[]\"'“”«»").strip()


def extract_description(raw: str) -> tuple:
    """(نص الوصف النظيف، وسوم اقترحها النموذج). يقبل JSON (كائن أو قائمة أو نص JSON) أو نصاً عادياً."""
    data = _parse_json(raw)
    if data is None:
        return _clean_text(raw), []
    tags: List[str] = []
    body: List[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in _TAG_KEYS:
                tags += [t for item in _strings(value) for t in re.findall(r"#?[\w\u0600-\u06FF]+", item)]
        wanted = next((k for k in data if str(k).lower() in _TEXT_KEYS), None)
        body = _strings(data[wanted]) if wanted is not None else _strings(
            {k: v for k, v in data.items() if str(k).lower() not in _TAG_KEYS}
        )
    else:
        body = _strings(data)
    text = _clean_text("\n\n".join(part for part in body if part.strip()))
    return text, ["#" + t.lstrip("#") for t in tags if t.lstrip("#")]


def suggested_hashtags(factory: models.Factory, description: str, model_tags: List[str] = ()) -> List[str]:
    """وسوم العلامة + وسم باسم المصنع + كلمات من نشاطه، دون تكرار وسم موجود في نص الوصف."""
    name_tag = "#" + "_".join(_WORD.findall(factory.name))
    candidates = list(BRAND_TAGS) + [name_tag] + list(model_tags)
    candidates += ["#" + word for word in _WORD.findall(factory.activity or "")]
    present = set(_HASHTAG.findall(description))
    tags = list(dict.fromkeys(tag for tag in candidates if len(tag) > 1 and tag not in present))
    return tags[: len(BRAND_TAGS) + 1 + MAX_EXTRA_TAGS + len(model_tags)]


def build_description(factory: models.Factory, text: str) -> str:
    """رد Ollama (بعد فك JSON وحذف الرموز الزائدة) + رقم هاتف المصنع + وسوم مقترحة."""
    body, model_tags = extract_description(text)
    if not body:
        raise InvalidInputError("رد Ollama لا يحتوي نص وصف صالحاً")
    lines = [body, f"📲 للطلب أو الاستفسار عبر واتساب: {factory.phone_number}"]
    tags = suggested_hashtags(factory, body, model_tags)
    if tags:
        lines.append(" ".join(tags))
    return "\n\n".join(lines)


def create_reel_post(
    db: Session, factory_id: int, video_id: int, chat_id: int, fallback_response: str = None,
    fallback_video_url: str = None,
) -> models.Post:
    """video_id: معرّف مهمة توليد الفيديو (factory_video_jobs)، وchat_id: معرّف مهمة الوصف (factory_description_jobs).
    كلتاهما يجب أن تتبع المصنع وتكون succeeded. fallback_response: وصف بديل (من رسائل Ollama السابقة للمصنع)
    يُستعمل بدل ناتج مهمة الوصف حين فشلت، فلا يُشترط نجاحها.
    fallback_video_url: رابط فيديو من مكتبة المصنع يُستعمل بدل ناتج مهمة الفيديو حين فشلت (بلا SKU)."""
    factory = get_factory(db, factory_id)

    video_job = db.query(models.FactoryVideoJob).filter(
        models.FactoryVideoJob.id == video_id, models.FactoryVideoJob.factory_id == factory_id,
    ).first()
    if video_job is None:
        raise NotFoundError("مهمة توليد الفيديو غير موجودة لهذا المصنع")
    if fallback_video_url is not None:
        video_url, video_sku = fallback_video_url, None
    elif video_job.status != "succeeded" or not video_job.video_url:
        raise ConflictError(f"مهمة الفيديو لم تكتمل بعد (الحالة: {video_job.status})")
    else:
        video_url, video_sku = video_job.video_url, video_job.sku

    chat_job = db.query(models.FactoryDescriptionJob).filter(
        models.FactoryDescriptionJob.id == chat_id, models.FactoryDescriptionJob.factory_id == factory_id,
    ).first()
    if chat_job is None:
        raise NotFoundError("مهمة توليد الوصف غير موجودة لهذا المصنع")
    if fallback_response is not None:
        raw_description = fallback_response
    elif chat_job.status != "succeeded" or not (chat_job.response or "").strip():
        raise ConflictError(f"مهمة الوصف لم تكتمل بعد (الحالة: {chat_job.status})")
    else:
        raw_description = chat_job.response

    channel_ids = post_publisher.active_channel_ids(db, factory_id)
    if not channel_ids:
        raise InvalidInputError("لا توجد قنوات نشر مفعّلة مرتبطة بهذا المصنع")

    post = models.Post(
        factory_id=factory_id,
        media_type="reel",
        media_urls=[video_url],
        description=build_description(factory, raw_description),
        status=PostStatus.APPROVED,
        approval=PostApproval.APPROVED,
        approved_at=utc_now(),
        scheduled_at=None,
        sku=video_sku,
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
