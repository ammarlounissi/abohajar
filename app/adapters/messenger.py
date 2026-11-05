"""توافق خلفي للكود القديم؛ التنفيذ الجديد في ``app.channels.messenger``."""
from app.channels.messenger.adapter import process_payload
from app.channels.messenger.client import MessengerClient
from app.services.customer_support import (
    build_reply,
    detect_intent,
    get_product_by_facebook_id,
    get_reply,
)


async def send_private_reply(comment_id: str, message: str) -> dict:
    return await MessengerClient().send_private_reply(comment_id, message)


async def send_public_reply(comment_id: str, message: str) -> dict:
    return await MessengerClient().send_public_reply(comment_id, message)


async def handle_facebook_comment(db, fb_post_id: str, comment_id: str, comment_text: str, is_tester: bool = False):
    """واجهة توافقية غير متزامنة؛ استخدم ``process_payload`` مع webhook الجديد."""
    reply = build_reply(db, facebook_post_id=fb_post_id, text=comment_text)
    client = MessengerClient()
    result = await client.send_private_reply(comment_id, reply.text)
    if result.get("error", {}).get("code") == 100:
        result = await client.send_public_reply(comment_id, reply.text)
    return result


__all__ = [
    "build_reply",
    "detect_intent",
    "get_product_by_facebook_id",
    "get_reply",
    "handle_facebook_comment",
    "process_payload",
    "send_private_reply",
    "send_public_reply",
]
