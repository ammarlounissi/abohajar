"""إعدادات صفحة الزبون ونص منشوره بـ Gemini. لا تعرف شيئاً عن HTTP.

يعيد استعمال طابور أوصاف المصانع كما هو (ollama_chat: FactoryDescriptionJob وعامل
hadjret-description-worker)، على مصنع مساحة الزبون.
- الاسم والنشاط: اسم ذلك المصنع ونشاطه (factories.name/activity)، ومنهما يكتب Gemini. النشاط الفارغ
  يُجلب من صفحة فيسبوك المربوطة (التصنيف والنبذة) ويُحفظ. اسم مصنع المورّد لا يغيّره الزبون.
- واتساب والوسوم: رقم المصنع (factories.phone_number، عبر factories.update_factory كما في الإدارة فتبقى جهة
  اتصال واتساب متطابقة والرقم فريداً) ووسومه (factories.hashtags). تُلحق بالوصف كما هي ولا تمرّ بالنموذج.
  كل الإعدادات للمصنع والمصنع للصفحة، ولا يُربط شيء منها بالمستخدم. مصنع المورّد تديره الإدارة: اسمه ورقمه لا
  يغيّرهما الزبون (الرقم هو ما يتعرّف به بوت واتساب على صاحبه)."""
import logging
import re
from typing import List, Optional

import httpx
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.models import Channel, FactoryKind
from app.services import customer_workspaces, facebook_services, ollama_chat
from app.services import factories as factory_service
from app.services.errors import DuplicateError, InvalidInputError

logger = logging.getLogger(__name__)

# النموذج نفسه الافتراضي لريلز المصانع المجدولة (FactoryReelSchedule.model).
CAPTION_MODEL = "gemini-3.1-flash-lite"
MAX_ACTIVITY_CHARS = 500           # حد factories.activity في مسارات الإدارة (schemas.FactoryCreate)
PAGE_FIELDS = "category,about,description"
FACEBOOK_TIMEOUT = 10.0
_ACTIVE = ("queued", "running")
FRIENDLY_FAILURE = "تعذّرت كتابة الوصف الآن؛ أعد المحاولة بعد قليل"
MAX_MODEL_HASHTAGS = 5
_HASHTAG = re.compile(r"#\w{2,40}")


def _page_activity(page: dict) -> str:
    """«التصنيف: النبذة» من حقول الصفحة، مقصوصاً إلى حد النشاط. نص فارغ إن لم يوجد شيء."""
    category = str(page.get("category") or "").strip()
    about = " ".join(str(page.get("about") or page.get("description") or "").split())
    text = f"{category}: {about}" if category and about else (category or about)
    return text[:MAX_ACTIVITY_CHARS].strip()


def fetch_page_activity(
    page_id: str, token: str, *, transport: Optional[httpx.BaseTransport] = None,
) -> str:
    """نشاط الصفحة كما في فيسبوك. أي فشل (شبكة، رمز منتهٍ، حقل محجوب) يُسجَّل ويرجع نصاً فارغاً:
    الجلب مساعدة لا شرط، والزبون يكتب النشاط بنفسه إن لم يأتِ."""
    try:
        with httpx.Client(timeout=FACEBOOK_TIMEOUT, transport=transport) as client:
            response = client.get(
                f"{settings.graph_url}/{page_id}", params={"fields": PAGE_FIELDS},
                headers={"Authorization": f"Bearer {token}"},
            )
        body = response.json() if response.content else {}
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("error"):
            logger.warning("Facebook page fields request failed with HTTP %s", response.status_code)
            return ""
        return _page_activity(body)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Could not fetch Facebook page fields: %s", type(exc).__name__)
        return ""


def _fill_activity_from_facebook(db: Session, user_id: int, service_key: str, factory: models.Factory) -> None:
    if (factory.activity or "").strip():
        return
    channel = facebook_services.connected_channel(db, user_id, service_key)
    if channel is None or not channel.page_access_token:
        return
    activity = fetch_page_activity(channel.page_id or channel.remote_id, channel.page_access_token)
    if activity:
        factory.activity = activity
        db.commit()


def _admin_managed(factory: models.Factory) -> bool:
    return factory.kind != FactoryKind.CUSTOMER.value


def _profile_view(factory: models.Factory) -> dict:
    return {
        "name": factory.name,
        "admin_managed": _admin_managed(factory),
        "activity": factory.activity or "",
        "whatsapp": factory.phone_number or None,
        "hashtags": list(factory.hashtags or []),
    }


def get_profile(db: Session, user_id: int, service_key: str) -> dict:
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    _fill_activity_from_facebook(db, user_id, service_key, workspace.factory)
    return _profile_view(workspace.factory)


def _clear_phone(db: Session, factory: models.Factory) -> None:
    """يحذف الرقم وجهة اتصال واتساب المطابقة له (update_factory لا يقبل رقماً فارغاً)."""
    if factory.phone_number:
        digits = factory_service.normalize_identifier(Channel.WHATSAPP.value, factory.phone_number)
        for contact in list(factory.contacts):
            if contact.channel == Channel.WHATSAPP.value and contact.identifier == digits:
                factory.contacts.remove(contact)       # delete-orphan يحذفه عند الحفظ
    factory.phone_number = None


def update_profile(db: Session, user_id: int, service_key: str, changes: dict) -> dict:
    """يغيّر ما في changes فقط (name, activity, whatsapp, hashtags)، بعد تطبيعها في schemas.CustomerProfileUpdate.
    كله على مصنع الصفحة. مصنع المورّد: الاسم والرقم للإدارة."""
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    factory = workspace.factory
    managed = _admin_managed(factory)
    values: dict = {}
    if changes.get("name") is not None and changes["name"] != factory.name:
        if managed:
            raise InvalidInputError("اسم هذه الصفحة مرتبط بمصنع تديره الإدارة، ولا يُعدَّل من هنا")
        values["name"] = changes["name"]
    if changes.get("activity") is not None:
        values["activity"] = changes["activity"]
    if "hashtags" in changes:
        values["hashtags"] = changes["hashtags"] or None
    if "whatsapp" in changes and (changes["whatsapp"] or None) != factory.phone_number:
        if managed:
            raise InvalidInputError("رقم هذه الصفحة مرتبط بمصنع تديره الإدارة، ولا يُعدَّل من هنا")
        if changes["whatsapp"]:
            values["phone_number"] = changes["whatsapp"]
        else:
            _clear_phone(db, factory)
    try:
        factory = factory_service.update_factory(db, factory.id, values)   # يحفظ (ومعه حذف الرقم إن وُجد)
    except DuplicateError:
        db.rollback()
        raise InvalidInputError("هذا الرقم مسجّل لصفحة أو مصنع آخر؛ اكتب رقماً غيره") from None
    return _profile_view(factory)


def split_hashtags(text: str) -> tuple:
    """(النص، وسوم أسطره الأخيرة): السطر الذي كله وسوم في آخر رد النموذج يُفصل عن النص."""
    lines = text.strip().splitlines()
    tags: List[str] = []
    while lines:
        line = lines[-1].strip()
        words = line.split()
        if not line:
            lines.pop()
        elif all(word.startswith("#") for word in words):
            tags = _HASHTAG.findall(line) + tags
            lines.pop()
        else:
            break
    return "\n".join(lines).strip(), list(dict.fromkeys(tags))


def compose_caption(text: str, factory: models.Factory) -> str:
    """الوصف + سطر واتساب (بصيغة ريلز المصانع، factory_reel_posts.build_description) + الوسوم.
    الوسوم: وسوم إعدادات الصفحة إن وُجدت، وإلا ما اقترحه النموذج في سطره الأخير (حتى 5)؛ وفي الحالين
    لا يتكرر وسم ورد في النص."""
    body, model_tags = split_hashtags(text)
    lines = [body]
    if factory.phone_number:
        lines.append(f"📲 للطلب أو الاستفسار عبر واتساب: {factory.phone_number}")
    chosen = list(factory.hashtags or []) or model_tags[:MAX_MODEL_HASHTAGS]
    tags = [tag for tag in chosen if tag not in body]
    if tags:
        lines.append(" ".join(tags))
    return "\n\n".join(lines)


def _job_view(job: models.FactoryDescriptionJob, factory: models.Factory) -> dict:
    """caption يُركَّب عند القراءة بإعدادات الصفحة الحالية: تعديل الرقم بعد الكتابة يظهر في الطلب التالي."""
    failed = job.status == "failed"
    done = job.status == "succeeded" and bool(job.response)
    return {
        "id": job.id,
        "status": job.status,
        "caption": compose_caption(job.response, factory) if done else None,
        "error": FRIENDLY_FAILURE if failed else None,
        "error_detail": job.error if failed else None,
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }


def request_caption(db: Session, user_id: int, service_key: str) -> dict:
    """يضع وصفاً في الطابور ويعيد المهمة. إن كان للصفحة وصف قيد التوليد يُعاد هو (ضغطة مزدوجة لا تكرّر)."""
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    factory = workspace.factory
    active = (
        db.query(models.FactoryDescriptionJob)
        .filter(
            models.FactoryDescriptionJob.factory_id == factory.id,
            models.FactoryDescriptionJob.status.in_(_ACTIVE),
        )
        .order_by(models.FactoryDescriptionJob.id.desc())
        .first()
    )
    if active is not None:
        return _job_view(active, factory)
    _fill_activity_from_facebook(db, user_id, service_key, factory)
    if not (factory.activity or "").strip():
        raise InvalidInputError("اكتب نشاط صفحتك أولاً: منه يكتب الذكاء الاصطناعي الوصف")
    job = ollama_chat.enqueue_description_job(
        db, factory.id, CAPTION_MODEL, hashtags=not factory.hashtags,   # بلا وسوم في الإعدادات: يقترحها النموذج
    )
    return _job_view(job, factory)


def get_caption_job(db: Session, user_id: int, service_key: str, job_id: int) -> dict:
    workspace = customer_workspaces.current_workspace(db, user_id, service_key)
    return _job_view(ollama_chat.get_description_job(db, workspace.factory_id, job_id), workspace.factory)
