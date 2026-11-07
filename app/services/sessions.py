"""رمز الجلسة: user_id وتاريخ انتهاء موقّعان بـ HMAC، يُحفظان في كوكي HttpOnly. لا تعرف شيئاً عن HTTP.

لا حالة على الخادم، فتعمل مع أي عدد من العمال. الإلغاء الفوري: تعطيل المستخدم (is_active) يوقف
جلسته عند الطلب التالي لأن كل طلب يعيد تحميل المستخدم."""
import base64
import hashlib
import hmac
import time
from typing import Optional

from app.core.config import settings


def _key() -> bytes:
    secret = settings.SESSION_SECRET or settings.META_APP_SECRET
    if not secret:
        raise RuntimeError("SESSION_SECRET أو META_APP_SECRET مطلوب لتوقيع الجلسات")
    return hashlib.sha256(b"hb-session-v1:" + secret.encode()).digest()


def _sign(payload: str) -> str:
    mac = hmac.new(_key(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def make_token(user_id: int, max_age: Optional[int] = None) -> str:
    expires = int(time.time()) + (max_age or settings.SESSION_MAX_AGE)
    payload = f"{user_id}.{expires}"
    return f"{payload}.{_sign(payload)}"


def read_token(token: Optional[str]) -> Optional[int]:
    """user_id إن كان الرمز سليماً وغير منتهٍ، وإلا None."""
    try:
        user_id, expires, signature = (token or "").split(".")
        if not hmac.compare_digest(signature, _sign(f"{user_id}.{expires}")):
            return None
        if int(expires) < time.time():
            return None
        return int(user_id)
    except (ValueError, TypeError):
        return None
