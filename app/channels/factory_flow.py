"""سير محادثة المصنع، مشترك بين كل القنوات.

يستقبل IncomingEvent موحّداً ويردّ عبر ChannelClient، ويستدعي services/ فقط.
لإضافة قناة جديدة لا تعدّل هذا الملف: اكتب محوّلاً يبني IncomingEvent وعميلاً ينفّذ ChannelClient.
"""
import logging
import re

from sqlalchemy.orm import Session

from app import models
from app.channels import texts
from app.channels.base import ChannelClient, EventKind, IncomingEvent
from app.services import factories, product_intake
from app.services.admin_command import is_import_command
from app.services.import_job import ImportBusy, run_import_job
from app.services.import_report import ImportConfigError

logger = logging.getLogger(__name__)

# معرّف زر سؤال التحديث: upd:yes:<id> / upd:no:<id>
_CHOICE_ID = re.compile(r"upd:(yes|no):(\d+)")


def _mask(identifier: str) -> str:
    return f"***{identifier[-4:]}"


async def handle_event(db: Session, event: IncomingEvent, client: ChannelClient) -> None:
    # أمر المدير يُفحص قبل البحث عن المصنع: المدير ليس بالضرورة مصنعاً
    text = event.text if event.kind is EventKind.TEXT else None
    if is_import_command(event.channel, event.sender_id, text):
        logger.info("Import command received from %s via %s", _mask(event.sender_id), event.channel)
        await _run_import_command(event, client)
        return

    factory = factories.find_by_contact(db, event.channel, event.sender_id)
    if not factory:
        logger.warning("Ignoring %s message from unregistered sender %s", event.channel, _mask(event.sender_id))
        return

    logger.info("%s event (%s) from factory %s", event.channel, event.kind.value, factory.id)
    if event.kind is EventKind.TEXT:
        await _handle_text(db, factory, event, client)
    elif event.kind is EventKind.IMAGE:
        await _handle_image(db, factory, event, client)
    elif event.kind is EventKind.CHOICE:
        await _handle_choice(db, factory, event, client)


async def _handle_text(db: Session, factory: models.Factory, event: IncomingEvent, client: ChannelClient) -> None:
    raw_text = event.text.strip()

    # جواب نعم/لا نصاً (بديل عن الأزرار) يخص آخر طلب معلّق لهذا المصنع في هذه القناة
    answer = texts.yes_no(raw_text)
    if answer is not None:
        pending = product_intake.find_pending(db, factory, event.channel)
        if pending:
            await _resolve_pending(db, factory, event, client, pending, accept=answer)
            return

    if texts.is_greeting(raw_text):
        reply = texts.greeting(factory)
    elif texts.is_add_product_request(raw_text):
        reply = texts.add_product_help(factory)
    else:
        reply = texts.GENERIC_WELCOME
    await client.send_text(event.sender_id, reply)


async def _handle_image(db: Session, factory: models.Factory, event: IncomingEvent, client: ChannelClient) -> None:
    result = product_intake.prepare_submission(
        db, factory, caption=event.caption.strip(), media_ref=event.media_ref
    )
    if isinstance(result, product_intake.Rejected):
        await client.send_text(event.sender_id, texts.rejection(result.code, result.sku))
        return

    # المنتج أو مجلده موجود: لا نكتب شيئاً قبل أن يوافق المصنع صراحةً على التحديث
    if result.exists:
        pending = product_intake.request_update_confirmation(db, factory, event.channel, result)
        await client.send_choices(
            event.sender_id,
            texts.update_question(result.sku, result.title, result.price),
            [(f"upd:yes:{pending.id}", "نعم، حدّث"), (f"upd:no:{pending.id}", "لا، ألغِ")],
        )
        return
    await _store(db, factory, event, client, result)


async def _handle_choice(db: Session, factory: models.Factory, event: IncomingEvent, client: ChannelClient) -> None:
    match = _CHOICE_ID.fullmatch(event.choice_id)
    if not match:
        return
    # نشترط أن يكون الطلب لهذا المصنع وهذه القناة بالذات، فلا يستطيع أحد الرد على طلب غيره
    pending = product_intake.find_pending(db, factory, event.channel, pending_id=int(match.group(2)))
    await _resolve_pending(db, factory, event, client, pending, accept=match.group(1) == "yes")


async def _resolve_pending(
    db: Session, factory: models.Factory, event: IncomingEvent, client: ChannelClient,
    pending, accept: bool,
) -> None:
    if pending is None:
        await client.send_text(event.sender_id, texts.PENDING_EXPIRED)
        return
    submission = product_intake.consume_pending(db, pending)
    if not accept:
        await client.send_text(event.sender_id, texts.update_cancelled(submission.sku))
        return
    await _store(db, factory, event, client, submission)


async def _store(
    db: Session, factory: models.Factory, event: IncomingEvent, client: ChannelClient,
    submission: product_intake.Submission,
) -> None:
    content = await client.download_media(submission.media_ref)
    result = await product_intake.store_product(db, factory, submission, content)
    if result.status is not product_intake.StoreStatus.STORED:
        await client.send_text(event.sender_id, texts.store_failure(result.status, submission.sku))
        return
    await client.send_text(event.sender_id, texts.stored(result.product, result.created, result.synced))


async def _run_import_command(event: IncomingEvent, client: ChannelClient) -> None:
    """الاستيراد التلقائي (كتابة + مزامنة ميتا) ثم ملخص. لا نسجّل نص الرسالة أبداً."""
    to = event.sender_id

    async def started():
        await client.send_text(to, texts.IMPORT_STARTED)

    async def written(count: int):
        await client.send_text(to, texts.import_written(count))

    try:
        result = await run_import_job(on_started=started, on_written=written)
    except ImportBusy:
        await client.send_text(to, texts.IMPORT_BUSY)
    except ImportConfigError as exc:
        await client.send_text(to, f"❌ {exc}")
    except Exception:
        logger.exception("Import command failed")
        await client.send_text(to, texts.IMPORT_FAILED)
    else:
        await client.send_text(to, texts.import_summary(result))
