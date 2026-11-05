"""عميل Meta لقناة Messenger وتعليقات Facebook فقط."""
import json
from typing import Optional
import httpx

from app.core.config import settings


class MessengerClient:
    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None, page_id: Optional[str] = None):
        self.page_id = page_id or settings.META_PAGE_ID
        self.transport = transport

    async def _post(self, path: str, data: dict) -> dict:
        payload = {**data, "access_token": settings.META_PAGE_ACCESS_TOKEN}
        async with httpx.AsyncClient(timeout=20, transport=self.transport) as client:
            response = await client.post(f"{settings.graph_url}/{path.lstrip('/')}", data=payload)
            try:
                body = response.json()
            except ValueError:
                body = {"error": {"message": response.text[:500]}}
            if response.status_code >= 400 and "error" not in body:
                body["error"] = {"message": f"http_{response.status_code}"}
            return body

    async def send_private_reply(self, comment_id: str, message: str) -> dict:
        """يرسل رسالة خاصة إلى صاحب تعليق Facebook.

        تستخدم Meta حاليًا Send API عبر ``/{page_id}/messages``.
        endpoint ``/{comment_id}/private_replies`` قديم ولا يعمل مع الإصدارات الحالية.
        """
        return await self._post(
            f"{self.page_id}/messages",
            {
                "recipient": json.dumps({"comment_id": comment_id}),
                "message": json.dumps({"text": message}, ensure_ascii=False),
                "messaging_type": "RESPONSE",
            },
        )

    async def send_public_reply(self, comment_id: str, message: str) -> dict:
        """ينشر ردًا عامًا على تعليق Facebook."""
        return await self._post(f"{comment_id}/comments", {"message": message})

    async def send_message(self, recipient_id: str, message: str) -> dict:
        return await self._post(
            f"{self.page_id}/messages",
            {
                "recipient": json.dumps({"id": recipient_id}),
                "message": json.dumps({"text": message}, ensure_ascii=False),
                "messaging_type": "RESPONSE",
            },
        )
