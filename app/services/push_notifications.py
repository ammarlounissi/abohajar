"""إشعارات المتصفح (Web Push) للوحة الزبون. لا تعرف شيئاً عن HTTP.

الجهاز يشترك من لوحة صفحةٍ ما فيُحفظ اشتراكه تابعاً لمصنع تلك الصفحة (لا للمستخدم: الإشعار يخص الصفحة).
يُرسل إليه حين:
- تنتهي كتابة المسودات فتصير بانتظار المراجعة (post_drafts.advance_captions)،
- يتعذّر تجهيز منشور معتمد (post_drafts._fail)،
- يُنشر منشور على الصفحة أو يفشل نشره (post_publisher.publish_post).

الإرسال لا يرفع استثناءً أبداً: فشل الإشعار لا يوقف عاملاً ولا نشراً. الاشتراك الذي تقول خدمة الدفع إنه
انتهى (404/410) يُحذف، وما يفشل MAX_FAILURES مرة متتالية يُحذف أيضاً.

عنوان الاشتراك يأتي من المتصفح والخادم يرسل إليه طلباً، فلا يُقبل إلا عنوان https لخدمة دفع معروفة
(لا يُستعمل الخادم لطلب عناوين داخلية)."""
import asyncio
import base64
import binascii
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urlsplit

from pywebpush import WebPushException, webpush
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.core.time import utc_now
from app.services import customer_workspaces
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

logger = logging.getLogger(__name__)

# خدمات الدفع في المتصفحات المعروفة: Chrome وEdge وSamsung وOpera على Android (FCM)، وFirefox، وSafari، وEdge على Windows.
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com", "push.apple.com",
              "notify.windows.com")
MAX_ENDPOINT = 1000
MAX_DEVICES = 20            # لكل صفحة؛ الأقدم يُحذف حين يُضاف جهاز فوق الحد
MAX_FAILURES = 5
SEND_TIMEOUT = 10           # ثوانٍ لكل جهاز
GONE = (404, 410)

TEST_MESSAGE_TITLE = "الإشعارات تعمل"


@dataclass(frozen=True)
class Message:
    title: str
    body: str
    page: str = ""          # القسم في لوحة الخدمة: "" للرئيسية، "schedule" للمجدول، "publish" للنشر اليدوي
    tag: str = ""           # إشعار بالوسم نفسه يحل محل السابق على الجهاز بدل أن يتراكم
    ttl: int = 24 * 3600    # كم تحتفظ به خدمة الدفع إن كان الجهاز مطفأً


@dataclass(frozen=True)
class _Target:
    id: int
    service_key: str
    info: dict


def available() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def _digest(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


def _b64url_bytes(value: str) -> bytes:
    value = value.strip()
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise InvalidInputError("مفاتيح الاشتراك غير صالحة") from exc


def _check_endpoint(endpoint: str) -> str:
    endpoint = (endpoint or "").strip()
    if not endpoint or len(endpoint) > MAX_ENDPOINT:
        raise InvalidInputError("عنوان الاشتراك غير صالح")
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.port not in (None, 443) or parts.username or parts.password:
        raise InvalidInputError("عنوان الاشتراك غير صالح")
    if not any(host == known or host.endswith("." + known) for known in PUSH_HOSTS):
        raise InvalidInputError("خدمة الإشعارات في هذا المتصفح غير مدعومة")
    return endpoint


def _check_keys(p256dh: str, auth: str) -> None:
    point, secret = _b64url_bytes(p256dh), _b64url_bytes(auth)
    if len(point) != 65 or point[0] != 4 or len(secret) != 16:
        raise InvalidInputError("مفاتيح الاشتراك غير صالحة")


# ───── الاشتراك من اللوحة ─────

def _factory_id(db: Session, user_id: int, service_key: str) -> int:
    return customer_workspaces.current_workspace(db, user_id, service_key).factory_id


def _find(db: Session, factory_id: int, endpoint: str) -> Optional[models.PushSubscription]:
    return db.query(models.PushSubscription).filter(
        models.PushSubscription.factory_id == factory_id,
        models.PushSubscription.endpoint_sha256 == _digest(endpoint),
    ).first()


def _count(db: Session, factory_id: int) -> int:
    return db.query(models.PushSubscription).filter(models.PushSubscription.factory_id == factory_id).count()


def status(db: Session, user_id: int, service_key: str, endpoint: Optional[str] = None) -> dict:
    """ما تحتاجه اللوحة: هل الإشعارات مفعّلة على الخادم، ومفتاحه العام، وعدد أجهزة الصفحة،
    وهل هذا الجهاز (endpoint) مشترك."""
    factory_id = _factory_id(db, user_id, service_key)
    subscribed = bool(endpoint) and _find(db, factory_id, endpoint.strip()) is not None
    return {
        "available": available(),
        "public_key": settings.VAPID_PUBLIC_KEY if available() else None,
        "devices": _count(db, factory_id),
        "subscribed": subscribed,
    }


def subscribe(
    db: Session, user_id: int, service_key: str, *, endpoint: str, p256dh: str, auth: str,
    user_agent: Optional[str] = None,
) -> dict:
    """يحفظ اشتراك الجهاز لصفحة الخدمة، أو يحدّث مفاتيحه إن كان محفوظاً (متكرر الأمان)."""
    if not available():
        raise ConflictError("الإشعارات غير مفعّلة على الخادم بعد")
    factory_id = _factory_id(db, user_id, service_key)
    endpoint = _check_endpoint(endpoint)
    _check_keys(p256dh, auth)
    row = _find(db, factory_id, endpoint)
    if row is None:
        row = models.PushSubscription(factory_id=factory_id, endpoint=endpoint, endpoint_sha256=_digest(endpoint))
        db.add(row)
    row.service_key, row.p256dh, row.auth, row.failures = service_key, p256dh.strip(), auth.strip(), 0
    row.user_agent = (user_agent or "")[:300] or None
    db.flush()
    extra = _count(db, factory_id) - MAX_DEVICES
    if extra > 0:
        oldest = db.query(models.PushSubscription).filter(
            models.PushSubscription.factory_id == factory_id, models.PushSubscription.id != row.id,
        ).order_by(models.PushSubscription.created_at, models.PushSubscription.id).limit(extra).all()
        for old in oldest:
            db.delete(old)
    db.commit()
    return status(db, user_id, service_key, endpoint)


def unsubscribe(db: Session, user_id: int, service_key: str, endpoint: str) -> None:
    """يوقف إشعارات صفحة الخدمة على هذا الجهاز. متكرر الأمان (لا خطأ إن لم يكن مشتركاً)."""
    factory_id = _factory_id(db, user_id, service_key)
    row = _find(db, factory_id, (endpoint or "").strip())
    if row is not None:
        db.delete(row)
        db.commit()


def send_test(db: Session, user_id: int, service_key: str, endpoint: str) -> bool:
    """إشعار تجريبي لهذا الجهاز وحده. NotFoundError إن لم يكن مشتركاً في إشعارات الصفحة."""
    if not available():
        raise ConflictError("الإشعارات غير مفعّلة على الخادم بعد")
    factory_id = _factory_id(db, user_id, service_key)
    row = _find(db, factory_id, (endpoint or "").strip())
    if row is None:
        raise NotFoundError("هذا الجهاز غير مشترك في الإشعارات")
    message = Message(TEST_MESSAGE_TITLE, "ستصلك هنا إشعارات صفحتك: المسودات الجاهزة والمنشورات.", tag="test",
                      ttl=600)
    stale, sent = _deliver([_target(row)], message)
    _settle(db, stale, sent)
    return bool(sent)


# ───── الإرسال ─────

def _target(row: models.PushSubscription) -> _Target:
    return _Target(row.id, row.service_key, {"endpoint": row.endpoint, "keys": {"p256dh": row.p256dh, "auth": row.auth}})


def _targets(db: Session, factory_id: int) -> List[_Target]:
    rows = db.query(models.PushSubscription).filter(models.PushSubscription.factory_id == factory_id).all()
    return [_target(row) for row in rows]


def _payload(message: Message, service_key: str) -> str:
    path = f"/services/{service_key}/dashboard/" + (f"{message.page}/" if message.page else "")
    return json.dumps(
        {"title": message.title, "body": message.body[:300], "url": path, "tag": message.tag},
        ensure_ascii=False,
    )


def _post(info: dict, data: str, ttl: int) -> None:
    """طلب واحد إلى خدمة الدفع (مستقل لتستبدله الاختبارات). يرفع WebPushException عند الرفض."""
    audience = "{0.scheme}://{0.netloc}".format(urlsplit(info["endpoint"]))
    webpush(
        info, data=data, vapid_private_key=settings.VAPID_PRIVATE_KEY,
        vapid_claims={"sub": settings.VAPID_SUBJECT, "aud": audience}, ttl=ttl, timeout=SEND_TIMEOUT,
        headers={"Urgency": "normal"},
    )


def _deliver(targets: List[_Target], message: Message) -> tuple[List[int], List[int]]:
    """يرسل إلى كل جهاز. لا يلمس قاعدة البيانات (يُشغَّل في خيط منفصل من الكود غير المتزامن).
    يرجع (المنتهية التي تُحذف، والتي وصلها). الفاشلة لسبب آخر في أي منهما لا تظهر."""
    gone: List[int] = []
    sent: List[int] = []
    for target in targets:
        try:
            _post(target.info, _payload(message, target.service_key), message.ttl)
            sent.append(target.id)
        except WebPushException as exc:
            code = getattr(exc.response, "status_code", None)
            if code in GONE:
                gone.append(target.id)
            else:
                logger.warning("Web push to subscription %s failed (%s): %s", target.id, code, exc)
        except Exception:
            logger.exception("Web push to subscription %s crashed", target.id)
    return gone, sent


def _settle(db: Session, gone: List[int], sent: List[int], tried: Optional[List[int]] = None) -> None:
    """يحذف المنتهية، ويصفّر عدّاد ما وصل، ويزيد عدّاد ما فشل (ويحذفه عند MAX_FAILURES)."""
    now = utc_now()
    for row in db.query(models.PushSubscription).filter(
        models.PushSubscription.id.in_(set(gone) | set(sent) | set(tried or [])),
    ).all():
        if row.id in gone:
            db.delete(row)
        elif row.id in sent:
            row.failures, row.last_sent_at = 0, now
        else:
            row.failures = (row.failures or 0) + 1
            if row.failures >= MAX_FAILURES:
                db.delete(row)
    db.commit()


def notify(db: Session, factory_id: int, message: Message) -> int:
    """يرسل الإشعار إلى كل أجهزة الصفحة ويرجع عدد ما وصلها. لا يرفع استثناءً."""
    if not available():
        return 0
    try:
        targets = _targets(db, factory_id)
        if not targets:
            return 0
        gone, sent = _deliver(targets, message)
        _settle(db, gone, sent, [t.id for t in targets])
        return len(sent)
    except Exception:
        db.rollback()
        logger.exception("Notifying factory %s failed", factory_id)
        return 0


async def notify_async(db: Session, factory_id: int, message: Message) -> int:
    """مثل notify للكود غير المتزامن (الناشر): الطلبات تُرسل في خيط منفصل فلا تعطّل حلقة الأحداث."""
    if not available():
        return 0
    try:
        targets = _targets(db, factory_id)
        if not targets:
            return 0
        gone, sent = await asyncio.to_thread(_deliver, targets, message)
        _settle(db, gone, sent, [t.id for t in targets])
        return len(sent)
    except Exception:
        db.rollback()
        logger.exception("Notifying factory %s failed", factory_id)
        return 0


# ───── رسائل الأحداث ─────

def drafts_ready(count: int) -> Message:
    return Message("مسوداتك جاهزة للمراجعة", f"عدد المسودات بانتظار موافقتك: {count}. لا يُنشر شيء قبل أن توافق.",
                   page="schedule", tag="drafts-ready")


def draft_failed(draft_id: int, reason: str) -> Message:
    return Message("منشور مجدول يحتاج انتباهك", reason, page="schedule", tag=f"draft-{draft_id}")


def _excerpt(text: Optional[str], limit: int = 90) -> str:
    line = next((part.strip() for part in (text or "").splitlines() if part.strip()), "")
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def _post_section(scheduled: bool) -> str:
    """قسم اللوحة الذي يُفتح من إشعار منشور: المجدول، أو «نشر الفيديو» حيث يُنشر الفيديو يدوياً ويُتابَع."""
    return "schedule" if scheduled else "publish"


def post_published(post_id: int, caption: Optional[str], scheduled: bool) -> Message:
    return Message("نُشر منشورك على صفحتك", _excerpt(caption) or "نُشر الفيديو على صفحتك في فيسبوك.",
                   page=_post_section(scheduled), tag=f"post-{post_id}", ttl=6 * 3600)


def post_failed(post_id: int, scheduled: bool) -> Message:
    body = "افتح اللوحة واختر موعداً جديداً لإعادة المحاولة." if scheduled else "افتح اللوحة لإعادة المحاولة."
    return Message("تعذّر نشر منشور على صفحتك", body, page=_post_section(scheduled), tag=f"post-{post_id}")
