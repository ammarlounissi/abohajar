import httpx
import os
import uuid
from dotenv import load_dotenv

load_dotenv()

WHATSAPP_TOKEN = os.getenv("META_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "1271219969411659")
WHATSAPP_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
BASE_URL = os.getenv("BASE_URL", "https://hadjra.ikhlasdz.com")
STATIC_ROOT = "static/uploads/factories"


async def send_whatsapp_message(to_phone: str, text: str):
    """إرسال رسالة نصية عبر WhatsApp Cloud API مع طباعة الاستجابة للتشخيص"""
    token = os.getenv("META_ACCESS_TOKEN", "").strip()
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "1271219969411659").strip()
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text}
    }

    print(f"🚀 Sending reply to {to_phone} via Phone ID: {phone_id}...")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            res_json = response.json()
            print(f"📡 Meta API Response [{response.status_code}]:", res_json)
            return res_json
        except Exception as e:
            print(f"❌ Network Error sending WhatsApp message: {e}")
            return None


async def download_and_save_whatsapp_media(media_id: str, factory_id: int, media_type: str = "image") -> str:
    """تحميل الوسائط وحفظها داخل مجلد المصنع المخصص: static/uploads/factories/factory_{id}/images/ أو /videos/"""
    token = os.getenv("META_ACCESS_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"}

    sub_folder = "videos" if media_type == "video" else "images"
    extension = "mp4" if media_type == "video" else "jpg"

    target_dir = os.path.join(STATIC_ROOT, f"factory_{factory_id}", sub_folder)
    os.makedirs(target_dir, exist_ok=True)

    async with httpx.AsyncClient() as client:
        try:
            # 1. جلب رابط التنزيل من Meta
            media_res = await client.get(f"https://graph.facebook.com/v19.0/{media_id}", headers=headers)
            if media_res.status_code != 200:
                print(f"❌ Failed to get media info from Meta: {media_res.text}")
                return ""

            download_url = media_res.json().get("url")
            if not download_url:
                return ""

            # 2. تحميل وحفظ الملف محلياً
            file_res = await client.get(download_url, headers=headers)
            if file_res.status_code == 200:
                filename = f"{media_type}_{uuid.uuid4().hex[:10]}.{extension}"
                file_path = os.path.join(target_dir, filename)

                with open(file_path, "wb") as f:
                    f.write(file_res.content)

                public_url = f"{BASE_URL}/static/uploads/factories/factory_{factory_id}/{sub_folder}/{filename}"
                print(f"✅ Saved {media_type} to: {public_url}")
                return public_url
            else:
                print(f"❌ Failed to download {media_type} content: {file_res.status_code}")

        except Exception as e:
            print(f"❌ Error downloading {media_type}: {e}")

    return ""