import logging
import uuid
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_MEDIA_BYTES = 15 * 1024 * 1024
EXTENSIONS = {
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "video/mp4": "mp4",
}


def _mask(phone: str) -> str:
    return f"***{phone[-4:]}"


async def send_whatsapp_message(to_phone: str, text: str) -> Optional[dict]:
    """إرسال رسالة نصية عبر WhatsApp Cloud API."""
    url = f"{settings.graph_url}/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {settings.META_ACCESS_TOKEN}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            logger.error("WhatsApp send failed [%s] to %s: %s",
                         response.status_code, _mask(to_phone), response.text)
        return response.json()
    except Exception:
        logger.exception("Network error sending WhatsApp message to %s", _mask(to_phone))
        return None


async def download_and_save_whatsapp_media(
    media_id: str, factory_id: int, media_type: str = "image"
) -> str:
    """تحميل وسائط واتساب وحفظها في static/uploads/factories/factory_{id}/{images|videos}/
    وإرجاع الرابط العام، أو نص فارغ عند الفشل."""
    headers = {"Authorization": f"Bearer {settings.META_ACCESS_TOKEN}"}
    sub_folder = "videos" if media_type == "video" else "images"
    target_dir = settings.UPLOADS_DIR / f"factory_{factory_id}" / sub_folder
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            info = await client.get(f"{settings.graph_url}/{media_id}", headers=headers)
            if info.status_code != 200:
                logger.error("Failed to get media info [%s]: %s", info.status_code, info.text)
                return ""
            info_json = info.json()
            download_url = info_json.get("url")
            if not download_url:
                return ""

            file_res = await client.get(download_url, headers=headers)
            if file_res.status_code != 200:
                logger.error("Failed to download media: %s", file_res.status_code)
                return ""
            if len(file_res.content) > MAX_MEDIA_BYTES:
                logger.error("Media too large (%s bytes)", len(file_res.content))
                return ""

        extension = EXTENSIONS.get(info_json.get("mime_type", ""), "mp4" if media_type == "video" else "jpg")
        filename = f"{media_type}_{uuid.uuid4().hex[:10]}.{extension}"
        (target_dir / filename).write_bytes(file_res.content)

        public_url = f"{settings.BASE_URL}/static/uploads/factories/factory_{factory_id}/{sub_folder}/{filename}"
        logger.info("Saved %s: %s", media_type, public_url)
        return public_url
    except Exception:
        logger.exception("Error downloading %s", media_type)
        return ""
