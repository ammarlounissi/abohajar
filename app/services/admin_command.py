"""التعرف على أوامر المدير الواردة عبر واتساب (بلا قاعدة بيانات، لذلك يسهل اختباره)."""
import hmac
import re

from app.core.config import settings


def is_import_command(from_phone: str, message: dict) -> bool:
    """الرسالة نصٌّ يطابق IMPORT_PASSWORD تماماً (دون حساسية لحالة الأحرف ولا للمسافات الطرفية)،
    ومن رقم مسموح إن قُيِّدت الأرقام في ADMIN_PHONE_NUMBERS. الميزة معطّلة إن كانت الكلمة فارغة."""
    if not settings.IMPORT_PASSWORD or message.get("type") != "text":
        return False
    text = (message.get("text") or {}).get("body") or ""
    # مقارنة بزمن ثابت كي لا يُستنتج من زمن الرد كم حرفاً صحيحاً
    if not hmac.compare_digest(
        text.strip().casefold().encode("utf-8"),
        settings.IMPORT_PASSWORD.casefold().encode("utf-8"),
    ):
        return False
    allowed = settings.ADMIN_PHONE_NUMBERS
    return not allowed or re.sub(r"\D", "", from_phone or "") in allowed
