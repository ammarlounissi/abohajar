import httpx
import os
from dotenv import load_dotenv

load_dotenv()

WHATSAPP_TOKEN = os.getenv("META_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

async def send_whatsapp_message(to_phone: str, text: str):
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN.strip() if WHATSAPP_TOKEN else ''}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text}
    }
    
    print(f"🚀 Sending reply to {to_phone} via Phone ID: {PHONE_NUMBER_ID}...")

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(WHATSAPP_URL, json=payload, headers=headers)
            res_json = response.json()
            print(f"📡 Meta API Response [{response.status_code}]:", res_json)
            return res_json
        except Exception as e:
            print(f"❌ Network Error sending WhatsApp message: {e}")
            return None

async def get_media_url(media_id: str) -> str:
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN.strip() if WHATSAPP_TOKEN else ''}"}
    url = f"https://graph.facebook.com/v19.0/{media_id}"
    
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                return res.json().get("url", "")
        except Exception as e:
            print(f"❌ Error getting media url: {e}")
    return ""