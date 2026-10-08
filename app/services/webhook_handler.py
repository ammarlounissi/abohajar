"""معالجة رسائل واتساب الواردة من المصانع (تعمل في الخلفية بعد الرد على ميتا)."""
import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.database import SessionLocal
from app.services import media
from app.services.admin_command import is_import_command
from app.services.import_report import ImportConfigError, format_report
from app.services.media_import import run_import, sync_touched
from app.services.meta import sync_product_to_meta
from app.services.products import SkuOwnershipError, parse_caption, upsert_product
from app.services.whatsapp import (
    download_whatsapp_media, send_whatsapp_buttons, send_whatsapp_message,
)

logger = logging.getLogger(__name__)

GREETING_KEYWORDS = [
    "السلام عليكم", "سلام عليكم", "السلام", "سلام", "مرحبا",
    "صباح الخير", "مساء الخير", "salam", "slm",
]


# مهلة جواب المصنع على سؤال "هل تريد التحديث؟"
PENDING_TTL = timedelta(hours=24)
YES_WORDS = {"نعم", "اي", "أي", "yes", "oui", "ok"}
NO_WORDS = {"لا", "no", "non"}
_BUTTON_ID = re.compile(r"upd:(yes|no):(\d+)")

# يمنع تشغيل استيرادين معاً (ضغط مزدوج أو إعادة إرسال)
_import_lock = asyncio.Lock()

SKU_HELP = "رمز المنتج حروف لاتينية كبيرة متبوعة بأرقام فقط، مثل B001 أو PTK012."


def check_greeting(text: str) -> bool:
    clean_text = text.lower().strip()
    return any(keyword in clean_text for keyword in GREETING_KEYWORDS)


def _mask(phone: str) -> str:
    return f"***{phone[-4:]}"


def _already_processed(db: Session, message_id: str) -> bool:
    """تسجيل الرسالة بشكل ذري؛ إن كانت مسجلة سابقاً فهي مكررة."""
    db.add(models.ProcessedMessage(message_id=message_id))
    try:
        db.commit()
        return False
    except IntegrityError:
        db.rollback()
        return True


def _find_factory(db: Session, from_phone: str):
    digits = re.sub(r"\D", "", from_phone)
    return (
        db.query(models.Factory)
        .filter(models.Factory.is_active.is_(True),
                models.Factory.phone_number.in_([digits, f"+{digits}"]))
        .first()
    )


async def process_payload(payload: dict) -> None:
    db = SessionLocal()
    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                for message in change.get("value", {}).get("messages", []):
                    try:
                        await _process_message(db, message)
                    except Exception:
                        db.rollback()
                        logger.exception("Failed processing message %s", message.get("id"))
    finally:
        db.close()


async def _process_message(db: Session, message: dict) -> None:
    message_id = message.get("id")
    from_phone = message.get("from")
    msg_type = message.get("type")
    if not message_id or not from_phone:
        return

    if _already_processed(db, message_id):
        logger.info("Duplicate message ignored: %s", message_id)
        return

    # أمر المدير يُفحص قبل البحث عن المصنع: رقم المدير ليس بالضرورة مصنعاً
    if is_import_command(from_phone, message):
        logger.info("Import command received from %s", _mask(from_phone))
        await _run_import_command(from_phone)
        return

    factory = _find_factory(db, from_phone)
    if not factory:
        logger.warning("Ignoring message from unregistered number %s", _mask(from_phone))
        return

    logger.info("Message %s (%s) from factory %s", message_id, msg_type, factory.id)

    if msg_type == "text":
        await _handle_text(db, factory, from_phone, message)
    elif msg_type == "image":
        await _handle_image(db, factory, from_phone, message)
    elif msg_type == "interactive":
        await _handle_interactive(db, factory, from_phone, message)


async def _handle_text(db: Session, factory: models.Factory, from_phone: str, message: dict) -> None:
    raw_text = message.get("text", {}).get("body", "").strip()

    # جواب نعم/لا نصاً (بديل عن الأزرار) يخص آخر طلب معلّق لهذا المصنع
    answer = _yes_no(raw_text)
    if answer is not None:
        pending = _latest_pending(db, factory)
        if pending:
            await _resolve_pending(db, factory, from_phone, pending, accept=answer)
            return

    if check_greeting(raw_text):
        reply = (
            "وعليكم السلام ورحمة الله وبركاته 🌸\n"
            f"أهلاً بك مصنع ({factory.name}) في منصة حجرة بلادي 🏛️.\n\n"
            "أرسل كلمة *إضافة منتج* للبدء في رفع منتجاتك."
        )
    elif "إضافة منتج" in raw_text or "اضافة منتج" in raw_text:
        reply = (
            f"أهلاً بك مصنع ({factory.name}) 🏛️\n\n"
            "لإضافة منتج جديد، أرسل *صورة الحجر* واكتب في الشرح (Caption) التنسيق التالي:\n\n"
            "*اسم المنتج - السعر - رمز SKU*\n\n"
            "📌 مثال:\n"
            "حجر قالمة بيج - 3200 - B001\n\n"
            "(ضع مسافة قبل الشرطة وبعدها)\n"
            f"{SKU_HELP}"
        )
    else:
        reply = "مرحباً بك! أرسل كلمة *إضافة منتج* للبدء في رفع منتجاتك."

    await send_whatsapp_message(from_phone, reply)


async def _run_import_command(from_phone: str) -> None:
    """يشغّل الاستيراد التلقائي مباشرة (كتابة + مزامنة ميتا) ويرسل ملخصاً. لا نسجّل نص الرسالة أبداً."""
    if _import_lock.locked():
        await send_whatsapp_message(from_phone, "⏳ عملية استيراد جارية الآن، انتظر انتهاءها.")
        return
    async with _import_lock:
        await send_whatsapp_message(from_phone, "⏳ بدأ الاستيراد من مجلد الصور...")
        try:
            # الفحص يقرأ ملفات كثيرة، فنشغّله في خيط منفصل كي لا يتجمد البوت
            report = await asyncio.to_thread(run_import, apply=True)
        except ImportConfigError as exc:
            await send_whatsapp_message(from_phone, f"❌ {exc}")
            return
        except Exception:
            logger.exception("Import command failed")
            await send_whatsapp_message(from_phone, "❌ فشل الاستيراد ولم تُكتب أي بيانات. راجع سجل الخادم.")
            return

        text = format_report(report, limit=5)
        if report.touched_ids:
            await send_whatsapp_message(
                from_phone, f"✅ كُتب {len(report.touched_ids)} منتجاً. 🔄 جارٍ المزامنة مع ميتا..."
            )
            try:
                failed = await sync_touched(report.touched_ids)
                text += f"\n\n🔄 مزامنة ميتا: نجحت {len(report.touched_ids) - failed}، فشلت {failed}"
            except Exception:
                logger.exception("Meta sync after import failed")
                text += "\n\n⚠️ تعذرت مزامنة ميتا، المنتجات محفوظة وحالتها pending."
        # حد رسالة واتساب النصية 4096 حرفاً
        await send_whatsapp_message(from_phone, text[:3800])


def _yes_no(text: str) -> Optional[bool]:
    word = text.strip().strip(".!؟?").lower()
    if word in YES_WORDS:
        return True
    if word in NO_WORDS:
        return False
    return None


def _pending_query(db: Session, factory: models.Factory):
    cutoff = datetime.utcnow() - PENDING_TTL
    return db.query(models.PendingMediaUpdate).filter(
        models.PendingMediaUpdate.factory_id == factory.id,
        models.PendingMediaUpdate.created_at >= cutoff,
    )


def _latest_pending(db: Session, factory: models.Factory):
    return _pending_query(db, factory).order_by(models.PendingMediaUpdate.id.desc()).first()


async def _handle_image(db: Session, factory: models.Factory, from_phone: str, message: dict) -> None:
    image = message.get("image", {})
    media_id = image.get("id")
    caption = (image.get("caption") or "").strip()

    parsed = parse_caption(caption)
    if not parsed:
        await send_whatsapp_message(
            from_phone,
            "⚠️ تنسيق الشرح غير صحيح. اكتب مع الصورة:\n"
            "*اسم المنتج - السعر - رمز SKU*\n"
            "مثال: حجر قالمة بيج - 3200 - B001",
        )
        return

    title, price, sku = parsed
    try:
        # نتحقق من البيانات قبل أي شيء آخر (الرابط مؤقت حتى يكتمل الحفظ)
        data = schemas.ProductBase(sku=sku, title=title, price=price, primary_media_url="pending")
    except ValidationError:
        await send_whatsapp_message(
            from_phone,
            "⚠️ بيانات غير صالحة. تأكد أن السعر رقم أكبر من الصفر، وأن الرمز صحيح.\n" + SKU_HELP,
        )
        return

    sku = data.sku
    # الـ SKU يُبنى منه مسار على القرص، فنقبل الصيغة الصارمة فقط
    if not media.is_valid_media_sku(sku):
        await send_whatsapp_message(from_phone, f"⚠️ رمز المنتج ({sku}) غير صالح.\n" + SKU_HELP)
        return
    if not media_id:
        await send_whatsapp_message(from_phone, "⚠️ تعذر قراءة الصورة، يرجى إعادة إرسالها.")
        return

    existing = db.query(models.Product).filter(models.Product.sku == sku).first()
    if existing and existing.factory_id != factory.id:
        await send_whatsapp_message(
            from_phone, f"⚠️ رمز المنتج ({sku}) مستخدم مسبقاً، يرجى اختيار رمز آخر."
        )
        return

    # المنتج أو مجلده موجود: لا نكتب شيئاً قبل أن يوافق المصنع صراحةً على التحديث
    if existing or media.has_media(sku):
        await _ask_to_update(db, factory, from_phone, sku, title, price, media_id)
        return

    await _store_product(db, factory, from_phone, sku, title, price, media_id)


async def _ask_to_update(
    db: Session, factory: models.Factory, from_phone: str,
    sku: str, title: str, price: float, media_id: str,
) -> None:
    # طلب جديد لنفس الرمز يلغي القديم
    db.query(models.PendingMediaUpdate).filter(
        models.PendingMediaUpdate.factory_id == factory.id,
        models.PendingMediaUpdate.sku == sku,
    ).delete(synchronize_session=False)
    pending = models.PendingMediaUpdate(
        factory_id=factory.id, sku=sku, title=title, price=price, media_id=media_id,
    )
    db.add(pending)
    db.commit()
    db.refresh(pending)

    await send_whatsapp_buttons(
        from_phone,
        f"⚠️ رمز المنتج ({sku}) موجود مسبقاً.\n\n"
        "هل تريد تحديثه بالصورة الجديدة؟\n"
        f"• تُستبدل الصورة الرئيسية (تُحفظ القديمة نسخةً احتياطية)\n"
        f"• يُحدَّث الاسم والسعر: {title} - {price:g} دج\n\n"
        "اضغط أحد الزرين، أو اكتب نعم / لا.",
        [(f"upd:yes:{pending.id}", "نعم، حدّث"), (f"upd:no:{pending.id}", "لا، ألغِ")],
    )


async def _handle_interactive(db: Session, factory: models.Factory, from_phone: str, message: dict) -> None:
    button_id = message.get("interactive", {}).get("button_reply", {}).get("id", "")
    match = _BUTTON_ID.fullmatch(button_id)
    if not match:
        return
    # نشترط أن يكون الطلب لهذا المصنع بالذات، فلا يستطيع مصنع الرد على طلب غيره
    pending = _pending_query(db, factory).filter(
        models.PendingMediaUpdate.id == int(match.group(2))
    ).first()
    await _resolve_pending(db, factory, from_phone, pending, accept=match.group(1) == "yes")


async def _resolve_pending(
    db: Session, factory: models.Factory, from_phone: str,
    pending: Optional[models.PendingMediaUpdate], accept: bool,
) -> None:
    if pending is None:
        await send_whatsapp_message(
            from_phone,
            "⚠️ هذا الطلب انتهت صلاحيته أو سبقت معالجته. أعد إرسال الصورة مع الشرح إن أردت.",
        )
        return

    sku, title, price, media_id = pending.sku, pending.title, pending.price, pending.media_id
    # نحذف الطلب قبل المعالجة، فلا يؤدي ضغطان متتاليان إلى تحديثين
    db.delete(pending)
    db.commit()

    if not accept:
        await send_whatsapp_message(
            from_phone, f"👍 تم إلغاء الطلب. لم يتغيّر شيء في المنتج ({sku})."
        )
        return
    await _store_product(db, factory, from_phone, sku, title, price, media_id)


async def _store_product(
    db: Session, factory: models.Factory, from_phone: str,
    sku: str, title: str, price: float, media_id: str,
) -> None:
    """تحميل الصورة وحفظها في مجلد المنتج، وإنشاء المنتج أو تحديثه، ثم مزامنة ميتا."""
    content = await download_whatsapp_media(media_id)
    if not content:
        await send_whatsapp_message(from_phone, "⚠️ تعذر تحميل الصورة، يرجى إعادة إرسالها.")
        return

    # الرابط يحمل الامتداد الفعلي، فنحدده من محتوى الصورة قبل بنائه
    ext = media.detect_image_extension(content)
    if ext is None:
        await send_whatsapp_message(
            from_phone, "⚠️ الملف ليس صورة مدعومة. أرسل صورة بصيغة JPEG أو PNG أو WebP."
        )
        return
    fields = {
        "sku": sku, "title": title, "price": price,
        "primary_media_url": media.public_image_url(sku, ext),
    }
    try:
        product, outcome = upsert_product(db, factory, fields)
    except SkuOwnershipError:
        db.rollback()
        await send_whatsapp_message(
            from_phone, f"⚠️ رمز المنتج ({sku}) مستخدم مسبقاً، يرجى اختيار رمز آخر."
        )
        return

    # الملف قبل commit: إن فشلت الكتابة نتراجع عن القاعدة فلا يبقى منتج بلا صورة
    try:
        media.save_main_image(sku, content)
    except (ValueError, OSError):
        db.rollback()
        logger.exception("Failed saving image for %s", sku)
        await send_whatsapp_message(
            from_phone, "⚠️ تعذر حفظ الصورة. تأكد أنها بصيغة JPEG أو PNG أو WebP وأعد إرسالها."
        )
        return
    db.commit()
    db.refresh(product)

    # نزامن دائماً، حتى لو لم تتغير بيانات النص، لأن الصورة نفسها تغيّرت
    result = await sync_product_to_meta(product.id, db)
    db.refresh(product)

    verb = "إضافة" if outcome == "created" else "تحديث"
    if result.get("status") == "success":
        await send_whatsapp_message(
            from_phone,
            f"✅ تم {verb} ومزامنة المنتج بنجاح!\n\n"
            f"📦 المنتج: {product.title}\n"
            f"💰 السعر: {product.price:g} دج\n"
            f"🏷️ الرمز: {product.sku}\n"
            f"🆔 معرّف ميتا: {product.meta_product_id}",
        )
    else:
        await send_whatsapp_message(
            from_phone,
            f"✅ تم حفظ المنتج ({product.sku}) لكن تعذرت مزامنته مع ميتا الآن.",
        )
