"""الخدمات التي تعمل عبر Facebook Login. لا تعرف شيئاً عن HTTP.

لكل خدمة أذوناتها هي فقط (لا نطلب من فيسبوك إلا ما تحتاجه الخدمة التي اختارها المستخدم)، وتعمل على
صفحة واحدة لكل مستخدم. خدمة جديدة = عنصر في SERVICES.

اتفاقية الحفظ: دوال هذا الملف لا تنفّذ commit؛ المستدعي يحفظ دفعة واحدة."""
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, List, Optional

from sqlalchemy.orm import Session

from app import models
from app.core.time import utc_now
from app.services.errors import ConflictError, InvalidInputError, NotFoundError

# مهلة اختيار الصفحة بعد العودة من فيسبوك. بعدها يُمحى توكن المستخدم المؤقت ويُطلب الربط من جديد.
PENDING_MAX_AGE = timedelta(minutes=15)

# الدخول بفيسبوك دون أي خدمة: الهوية فقط، بلا صفحات.
LOGIN_SCOPES = ("public_profile", "email")


@dataclass(frozen=True)
class FacebookService:
    key: str                                  # يطابق slug الخدمة في الواجهة (content/services.ts)
    name: str
    scopes: tuple[str, ...]                   # ما يُطلب من فيسبوك لهذه الخدمة، لا أكثر

    def missing_scopes(self, granted: Iterable[str]) -> List[str]:
        granted = set(granted)
        return [scope for scope in self.scopes if scope not in granted]


SERVICES = {
    service.key: service
    for service in (
        FacebookService(
            key="facebook-reels",
            name="توليد الفيديو والتعليقات",
            scopes=(
                "pages_show_list",          # عرض صفحات المستخدم ليختار منها
                "pages_manage_posts",       # نشر الفيديوهات على الصفحة
                "pages_read_engagement",    # قراءة التعليقات على منشوراتها
                "pages_manage_engagement",  # الرد على التعليقات
            ),
        ),
    )
}


def get_service(key: str) -> FacebookService:
    service = SERVICES.get((key or "").strip())
    if service is None:
        raise NotFoundError("الخدمة غير موجودة")
    return service


def _connection(db: Session, user_id: int, service: str) -> Optional[models.ServiceConnection]:
    return (
        db.query(models.ServiceConnection)
        .filter(models.ServiceConnection.user_id == user_id, models.ServiceConnection.service == service)
        .first()
    )


def _clear_pending(connection: models.ServiceConnection) -> None:
    connection.pending_pages = None
    connection.pending_user_token = None
    connection.pending_owner_name = None
    connection.pending_at = None


def _pending_pages(connection: Optional[models.ServiceConnection]) -> List[dict]:
    """الصفحات المنتظِرة اختيار المستخدم، أو [] إن لم توجد أو انتهت مهلتها (فتُمحى)."""
    if connection is None or not connection.pending_user_token or connection.pending_at is None:
        return []
    if utc_now() - connection.pending_at > PENDING_MAX_AGE:
        _clear_pending(connection)
        return []
    try:
        pages = json.loads(connection.pending_pages or "[]")
    except ValueError:
        pages = []
    return [page for page in pages if isinstance(page, dict) and page.get("id")]


def begin_selection(
    db: Session, *, user_id: int, service: str, pages: List[dict], user_token: str,
    owner_name: Optional[str],
) -> models.ServiceConnection:
    """المستخدم منح أكثر من صفحة: نحفظ أسماءها (بلا توكناتها) وتوكن المستخدم مؤقتاً حتى يختار واحدة.
    ربط الخدمة القائم (إن وُجد) يبقى كما هو حتى يتم الاختيار."""
    connection = _connection(db, user_id, service)
    if connection is None:
        connection = models.ServiceConnection(user_id=user_id, service=service)
        db.add(connection)
    connection.pending_pages = json.dumps(
        [{"id": str(page["id"]), "name": str(page.get("name") or "")} for page in pages],
        ensure_ascii=False,
    )
    connection.pending_user_token = user_token
    connection.pending_owner_name = owner_name
    connection.pending_at = utc_now()
    return connection


@dataclass(frozen=True)
class PendingChoice:
    page_id: str
    user_token: str
    owner_name: Optional[str]


def pending_choice(db: Session, user_id: int, service: str, page_id: str) -> PendingChoice:
    """يتحقق أن page_id من الصفحات المعروضة على هذا المستخدم لهذه الخدمة، ويرجع ما يلزم لجلب توكنها."""
    connection = _connection(db, user_id, service)
    pages = _pending_pages(connection)
    if not pages:
        raise ConflictError("انتهت مهلة اختيار الصفحة. أعد ربط صفحتك من جديد.")
    if page_id not in {page["id"] for page in pages}:
        raise InvalidInputError("هذه الصفحة ليست من الصفحات التي منحتَ الإذن لها")
    return PendingChoice(
        page_id=page_id, user_token=connection.pending_user_token, owner_name=connection.pending_owner_name,
    )


def activate(
    db: Session, *, user_id: int, service: str, channel: models.SocialChannel,
) -> models.ServiceConnection:
    """يجعل channel صفحة هذه الخدمة الوحيدة لهذا المستخدم، ويمحو أي اختيار معلّق."""
    connection = _connection(db, user_id, service)
    if connection is None:
        connection = models.ServiceConnection(user_id=user_id, service=service)
        db.add(connection)
    connection.channel = channel
    _clear_pending(connection)
    return connection


def connected_channel(db: Session, user_id: int, service: str) -> Optional[models.SocialChannel]:
    """الصفحة المربوطة بهذه الخدمة لهذا المستخدم، أو None. لا تُرجع صفحة لم تعد ملكه."""
    connection = _connection(db, user_id, service)
    channel = connection.channel if connection is not None else None
    return channel if channel is not None and channel.user_id == user_id else None


def cancel_selection(db: Session, user_id: int, service: str) -> None:
    """يلغي الاختيار المعلّق ويمحو التوكن المؤقت. صف بلا صفحة ولا اختيار معلّق لا معنى له فيُحذف."""
    connection = _connection(db, user_id, service)
    if connection is None:
        return
    _clear_pending(connection)
    if connection.channel_id is None:
        db.delete(connection)


def describe(db: Session, user_id: int, service: FacebookService) -> dict:
    """حالة الخدمة لهذا المستخدم كما تعرضها الواجهة. لا تكشف أي توكن."""
    connection = _connection(db, user_id, service.key)
    channel = connection.channel if connection is not None else None
    page = None
    if channel is not None and channel.user_id == user_id:
        page = {
            "page_id": channel.page_id or channel.remote_id,
            "page_name": channel.page_name or channel.name,
            "is_active": channel.is_active,
        }
    return {
        "service": service.key,
        "name": service.name,
        "scopes": list(service.scopes),
        "page": page,
        "candidates": [
            {"page_id": item["id"], "page_name": item.get("name") or ""}
            for item in _pending_pages(connection)
        ],
    }


def delete_for_user(db: Session, user_id: int) -> int:
    """يحذف كل ارتباطات الخدمات لمستخدم (عند سحب تصريح فيسبوك أو طلب حذف بياناته)."""
    return (
        db.query(models.ServiceConnection)
        .filter(models.ServiceConnection.user_id == user_id)
        .delete(synchronize_session=False)
    )
