import logging
from typing import List, Optional, Tuple

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_MEDIA_BYTES = 15 * 1024 * 1024


def _mask(phone: str) -> str:
    return f"***{phone[-4:]}"


async def _post_message(to_phone: str, payload: dict) -> Optional[dict]:
    url = f"{settings.graph_url}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {settings.META_ACCESS_TOKEN}"}
    body = {"messaging_product": "whatsapp", "to": to_phone, **payload}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json=body, headers=headers)
        if response.status_code >= 400:
            logger.error("WhatsApp send failed [%s] to %s: %s",
                         response.status_code, _mask(to_phone), response.text)
        return response.json()
    except Exception:
        logger.exception("Network error sending WhatsApp message to %s", _mask(to_phone))
        return None


async def send_whatsapp_message(to_phone: str, text: str) -> Optional[dict]:
    """إرسال رسالة نصية عبر WhatsApp Cloud API."""
    return await _post_message(to_phone, {"type": "text", "text": {"body": text}})


async def send_whatsapp_buttons(
    to_phone: str, text: str, buttons: List[Tuple[str, str]]
) -> Optional[dict]:
    """رسالة بأزرار رد سريع. buttons = [(id, title), ...] بحد أقصى 3،
    والعنوان حتى 20 حرفاً، والنص حتى 1024 حرفاً. يصل الضغط كرسالة type=interactive."""
    return await _post_message(to_phone, {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": text},
            "action": {"buttons": [
                {"type": "reply", "reply": {"id": bid, "title": title}}
                for bid, title in buttons
            ]},
        },
    })


async def send_whatsapp_list(
    to_phone: str, text: str, button_label: str,
    rows: List[Tuple[str, str, str]], section_title: str = "الخيارات",
) -> Optional[dict]:
    """رسالة قائمة منسدلة. rows = [(id, title, description), ...] بحد أقصى 10 صفوف،
    العنوان حتى 24 حرفاً، والوصف حتى 72 (يُحذف إن كان فارغاً)، وزر الفتح حتى 20 حرفاً.
    يصل الاختيار كرسالة type=interactive بحقل list_reply."""
    out_rows = []
    for rid, title, desc in rows[:10]:
        row = {"id": rid, "title": title[:24]}
        if desc:
            row["description"] = desc[:72]
        out_rows.append(row)
    return await _post_message(to_phone, {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": text[:1024]},
            "action": {
                "button": button_label[:20],
                "sections": [{"title": section_title[:24], "rows": out_rows}],
            },
        },
    })


async def download_whatsapp_media(media_id: str) -> Optional[bytes]:
    """تحميل وسائط واتساب (طلبان: معرّف الوسائط ثم الملف) وإرجاع محتواها،
    أو None عند الفشل. الحفظ على القرص من مسؤولية services/media.py."""
    headers = {"Authorization": f"Bearer {settings.META_ACCESS_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            info = await client.get(f"{settings.graph_url}/{media_id}", headers=headers)
            if info.status_code != 200:
                logger.error("Failed to get media info [%s]: %s", info.status_code, info.text)
                return None
            download_url = info.json().get("url")
            if not download_url:
                return None

            file_res = await client.get(download_url, headers=headers)
            if file_res.status_code != 200:
                logger.error("Failed to download media: %s", file_res.status_code)
                return None
            if len(file_res.content) > MAX_MEDIA_BYTES:
                logger.error("Media too large (%s bytes)", len(file_res.content))
                return None
            return file_res.content
    except Exception:
        logger.exception("Error downloading media %s", media_id)
        return None
