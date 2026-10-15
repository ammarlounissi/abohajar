"""التعرف على أوامر المدير الواردة نصاً من أي قناة (بلا قاعدة بيانات، لذلك يسهل اختباره)."""
import hmac
import re
from typing import Optional

from app.core.config import settings
from app.models import Channel


def _allowed_senders(channel: str) -> list:
    allowed = list(settings.ADMIN_CONTACTS.get(channel, []))
    if channel == Channel.WHATSAPP.value:
        allowed += settings.ADMIN_PHONE_NUMBERS
    return allowed


def _normalize(channel: str, sender_id: str) -> str:
    return re.sub(r"\D", "", sender_id or "") if channel == Channel.WHATSAPP.value else (sender_id or "").strip()


def is_import_command(channel: str, sender_id: str, text: Optional[str]) -> bool:
    """النص يطابق IMPORT_PASSWORD تماماً (دون حساسية لحالة الأحرف ولا للمسافات الطرفية)،
    ومن مرسل مسموح إن قُيِّد المرسلون لهذه القناة (ADMIN_CONTACTS، أو ADMIN_PHONE_NUMBERS لواتساب).
    الميزة معطّلة إن كانت الكلمة فارغة. text=None لرسائل ليست نصاً."""
    if not settings.IMPORT_PASSWORD or text is None:
        return False
    # مقارنة بزمن ثابت كي لا يُستنتج من زمن الرد كم حرفاً صحيحاً
    if not hmac.compare_digest(
        text.strip().casefold().encode("utf-8"),
        settings.IMPORT_PASSWORD.casefold().encode("utf-8"),
    ):
        return False
    allowed = _allowed_senders(channel)
    return not allowed or _normalize(channel, sender_id) in allowed
