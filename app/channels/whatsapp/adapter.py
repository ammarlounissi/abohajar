"""محوّل واتساب: يترجم حمولة Meta إلى IncomingEvent ويسلّمها لسير المحادثة المشترك.
(يعمل في الخلفية بعد الرد على ميتا بـ 200.)"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.channels import factory_flow
from app.channels.base import EventKind, IncomingEvent
from app.channels.whatsapp.client import WhatsAppClient
from app.core.database import SessionLocal
from app.models import Channel
from app.services.dedup import claim_message

logger = logging.getLogger(__name__)

_client = WhatsAppClient()


def to_event(message: dict) -> Optional[IncomingEvent]:
    """رسالة واتساب الخام -> حدث موحّد، أو None لأنواع غير مدعومة."""
    sender = message.get("from")
    if not sender:
        return None
    base = {"channel": Channel.WHATSAPP.value, "sender_id": sender}
    msg_type = message.get("type")
    if msg_type == "text":
        return IncomingEvent(kind=EventKind.TEXT, text=(message.get("text") or {}).get("body") or "", **base)
    if msg_type == "image":
        image = message.get("image") or {}
        return IncomingEvent(
            kind=EventKind.IMAGE, media_ref=image.get("id"), caption=image.get("caption") or "", **base
        )
    if msg_type == "interactive":
        reply = (message.get("interactive") or {}).get("button_reply") or {}
        return IncomingEvent(kind=EventKind.CHOICE, choice_id=reply.get("id") or "", **base)
    return None


async def process_payload(payload: dict) -> None:
    db = SessionLocal()
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for message in change.get("value", {}).get("messages", []):
                    try:
                        await process_message(db, message)
                    except Exception:
                        db.rollback()
                        logger.exception("Failed processing message %s", message.get("id"))
    finally:
        db.close()


async def process_message(db: Session, message: dict, client=_client) -> None:
    message_id = message.get("id")
    if not message_id:
        return
    event = to_event(message)
    if event is None:
        return
    if not claim_message(db, Channel.WHATSAPP.value, message_id):
        logger.info("Duplicate message ignored: %s", message_id)
        return
    await factory_flow.handle_event(db, event, client)
