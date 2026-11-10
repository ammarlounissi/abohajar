import hashlib
import hmac

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import settings

_docs_basic = HTTPBasic(auto_error=False)


def _safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def require_docs_login(credentials: HTTPBasicCredentials | None = Depends(_docs_basic)) -> None:
    """حماية التوثيق بنافذة دخول المتصفح: كلمة السر هي ADMIN_API_KEY، واسم المستخدم يُهمَل."""
    if (
        not credentials
        or not settings.ADMIN_API_KEY
        or not _safe_equal(credentials.password, settings.ADMIN_API_KEY)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="API docs"'},
        )


def require_admin(x_api_key: str = Header(default="")) -> None:
    """حماية مسارات /api/* بمفتاح إدارة يُرسل في الترويسة X-API-Key."""
    if not settings.ADMIN_API_KEY or not _safe_equal(x_api_key, settings.ADMIN_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


async def verify_signature(request: Request) -> None:
    """التحقق من أن طلب الـ webhook جاء فعلاً من Meta (X-Hub-Signature-256)."""
    body = await request.body()
    expected = "sha256=" + hmac.new(
        settings.META_APP_SECRET.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    received = request.headers.get("X-Hub-Signature-256", "")
    if not _safe_equal(received, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")
