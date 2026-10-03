"""معالجة رسائل واتساب الواردة من المصانع (تعمل في الخلفية بعد الرد على ميتا)."""
import logging
import re

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.database import SessionLocal
from app.services.meta import sync_product_to_meta
from app.services.products import parse_caption, upsert_product
from app.services.whatsapp import download_and_save_whatsapp_media, send_whatsapp_message

logger = logging.getLogger(__name__)

GREETING_KEYWORDS = [
    "السلام عليكم", "سلام عليكم", "السلام", "سلام", "مرحبا",
    "صباح الخير", "مساء الخير", "salam", "slm",
]


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

    factory = _find_factory(db, from_phone)
    if not factory:
        logger.warning("Ignoring message from unregistered number %s", _mask(from_phone))
        return

    logger.info("Message %s (%s) from factory %s", message_id, msg_type, factory.id)

    if msg_type == "text":
        await _handle_text(factory, from_phone, message)
    elif msg_type == "image":
        await _handle_image(db, factory, from_phone, message)


async def _handle_text(factory: models.Factory, from_phone: str, message: dict) -> None:
    raw_text = message.get("text", {}).get("body", "").strip()

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
            "حجر قالمة بيج - 3200 - STONE-GLM-01\n\n"
            "(ضع مسافة قبل الشرطة وبعدها)"
        )
    else:
        reply = "مرحباً بك! أرسل كلمة *إضافة منتج* للبدء في رفع منتجاتك."

    await send_whatsapp_message(from_phone, reply)


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
            "مثال: حجر قالمة بيج - 3200 - STONE-GLM-01",
        )
        return

    title, price, sku = parsed
    try:
        # نتحقق من البيانات قبل تحميل الصورة (الرابط مؤقت حتى يكتمل التحميل)
        data = schemas.ProductBase(sku=sku, title=title, price=price, primary_media_url="pending")
    except ValidationError:
        await send_whatsapp_message(
            from_phone,
            "⚠️ بيانات غير صالحة. تأكد أن السعر رقم أكبر من الصفر، "
            "وأن الرمز حروف لاتينية كبيرة وأرقام وشرطات فقط.",
        )
        return

    if db.query(models.Product).filter(models.Product.sku == data.sku).first():
        await send_whatsapp_message(
            from_phone, f"⚠️ رمز المنتج ({data.sku}) مستخدم مسبقاً، يرجى اختيار رمز آخر."
        )
        return

    image_url = await download_and_save_whatsapp_media(media_id, factory_id=factory.id, media_type="image")
    if not image_url:
        await send_whatsapp_message(from_phone, "⚠️ تعذر تحميل الصورة، يرجى إعادة إرسالها.")
        return

    fields = data.model_dump(include=data.model_fields_set)
    fields["primary_media_url"] = image_url
    product, _ = upsert_product(db, factory, fields)
    db.commit()
    db.refresh(product)

    result = await sync_product_to_meta(product.id, db)
    db.refresh(product)

    if result.get("status") == "success":
        await send_whatsapp_message(
            from_phone,
            f"✅ تم إضافة ومزامنة المنتج بنجاح!\n\n"
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
