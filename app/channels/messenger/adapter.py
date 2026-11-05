"""محوّل Meta: لا يحتوي على منطق تسعير أو معرفة بنماذج المنتجات."""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.channels.messenger.client import MessengerClient
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.customer_support import build_reply
from app.services.dedup import claim_message
from app.services.social_channels import get_active_facebook_page

logger = logging.getLogger(__name__)


def _comment_event(change: dict, page_id: Optional[str] = None) -> Optional[dict]:
    value = change.get("value") or {}
    page_id = str(page_id or value.get("page_id") or settings.META_PAGE_ID or "")
    if change.get("field") not in ("feed", "comments"):
        return None
    comment_id = value.get("comment_id") or value.get("id")
    post_id = value.get("post_id") or (value.get("post") or {}).get("id")
    text = value.get("message") or value.get("text") or ""
    sender_id = str((value.get("from") or {}).get("id") or "")
    if not comment_id or not post_id or not text or (page_id and sender_id == page_id):
        return None
    return {
        "message_id": str(comment_id), "comment_id": str(comment_id),
        "post_id": str(post_id), "page_id": page_id, "text": text,
    }


def _messenger_event(message: dict, entry: dict) -> Optional[dict]:
    sender_id = str((message.get("sender") or {}).get("id") or "")
    message_body = message.get("message") or {}
    message_id = message_body.get("mid")
    text = message_body.get("text") or ""
    recipient_id = str(
        ((message.get("recipient") or {}).get("id") or entry.get("id") or "")
    )
    if not sender_id or not message_id or not text or not recipient_id or sender_id == recipient_id:
        return None
    return {
        "message_id": str(message_id), "sender_id": sender_id,
        "recipient_id": recipient_id, "page_id": recipient_id, "text": text,
    }


async def process_comment(db: Session, event: dict, client: MessengerClient) -> None:
    if not get_active_facebook_page(db, event["page_id"]):
        logger.info("Ignoring comment from inactive or unregistered Facebook page %s", event["page_id"])
        return
    if not claim_message(db, "facebook_comment", event["message_id"]):
        return
    reply = build_reply(db, facebook_post_id=event["post_id"], text=event["text"])
    page_client = MessengerClient(page_id=event["page_id"], transport=client.transport)
    result = await page_client.send_private_reply(event["comment_id"], reply.text)
    error = result.get("error") if isinstance(result, dict) else None
    if error and error.get("code") == 100:
        await page_client.send_public_reply(event["comment_id"], reply.text)


async def process_message(db: Session, event: dict, client: MessengerClient) -> None:
    page = get_active_facebook_page(db, event["page_id"])
    if not page:
        logger.info("Ignoring Messenger message from inactive or unregistered Facebook page %s", event["page_id"])
        return
    if not claim_message(db, "messenger", event["message_id"]):
        return
    reply = build_reply(db, text=event["text"])
    page_client = MessengerClient(page_id=event["page_id"], transport=client.transport)
    await page_client.send_message(event["sender_id"], reply.text)


async def process_payload(payload: dict, client: Optional[MessengerClient] = None) -> None:
    """يعالج تعليقات Facebook ورسائل Messenger في الخلفية."""
    client = client or MessengerClient()
    db = SessionLocal()
    try:
        if payload.get("object") != "page":
            return
        for entry in payload.get("entry", []):
            page_id = str(entry.get("id") or "")
            if not get_active_facebook_page(db, page_id):
                logger.info("Ignoring webhook from inactive or unregistered Facebook page %s", page_id or "<missing>")
                continue
            for change in entry.get("changes", []):
                event = _comment_event(change, page_id)
                if event:
                    try:
                        await process_comment(db, event, client)
                    except Exception:
                        db.rollback()
                        logger.exception("Failed processing Facebook comment %s", event["message_id"])
            for message in entry.get("messaging", []):
                event = _messenger_event(message, entry)
                if event:
                    try:
                        await process_message(db, event, client)
                    except Exception:
                        db.rollback()
                        logger.exception("Failed processing Messenger message %s", event["message_id"])
    finally:
        db.close()
