import hashlib
import hmac

from fastapi import Header, HTTPException, Request, status

from app.core.config import settings


def _safe_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


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
