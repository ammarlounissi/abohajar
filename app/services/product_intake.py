"""استقبال منتج من مصنع: التحقق، سؤال التحديث، حفظ الوسائط، المزامنة.

هذه حالات استخدام (use cases) مشتركة بين كل القنوات. لا تعرف شيئاً عن واتساب ولا تيليجرام:
- تُرجع نتائج منظَّمة (رموز وبيانات) لا نصوصاً؛ كل قناة تصيغها بنصها.
- لا تنزّل الوسائط بنفسها؛ القناة تنزّل البايتات وتمررها (media_ref مجرد مرجع عند القناة).

التدفق:  prepare_submission  ->  (إن وُجد المنتج) request_update_confirmation  ->  find_pending/consume_pending
         ->  store_product
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Union

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import models, schemas
from app.services import media
from app.services.meta import sync_product_to_meta
from app.services.products import SkuOwnershipError, parse_caption, upsert_product

logger = logging.getLogger(__name__)

# مهلة جواب المصنع على سؤال "هل تريد التحديث؟"
PENDING_TTL = timedelta(hours=24)


class RejectionCode(str, Enum):
    BAD_CAPTION = "bad_caption"      # الشرح ليس "اسم - سعر - SKU"
    INVALID_DATA = "invalid_data"    # السعر أو العنوان أو الرمز لا يجتاز التحقق
    INVALID_SKU = "invalid_sku"      # الرمز لا يصلح لبناء مسار على القرص
    NO_MEDIA = "no_media"            # لم يصل مرجع للصورة
    SKU_TAKEN = "sku_taken"          # الرمز يتبع مصنعاً آخر


@dataclass(frozen=True)
class Rejected:
    code: RejectionCode
    sku: Optional[str] = None


@dataclass(frozen=True)
class Submission:
    """منتج جاهز للحفظ بعد اجتياز التحقق."""
    sku: str
    title: str
    price: float
    media_ref: str
    exists: bool          # المنتج أو مجلد وسائطه موجود: يلزم تأكيد المصنع قبل أي كتابة


def prepare_submission(
    db: Session, factory: models.Factory, *, caption: str, media_ref: Optional[str]
) -> Union[Submission, Rejected]:
    parsed = parse_caption(caption)
    if not parsed:
        return Rejected(RejectionCode.BAD_CAPTION)
    title, price, sku = parsed
    try:
        # نتحقق من البيانات قبل أي شيء آخر (الرابط مؤقت حتى يكتمل الحفظ)
        data = schemas.ProductBase(sku=sku, title=title, price=price, primary_media_url="pending")
    except ValidationError:
        return Rejected(RejectionCode.INVALID_DATA)

    sku = data.sku
    # الـ SKU يُبنى منه مسار على القرص، فنقبل الصيغة الصارمة فقط
    if not media.is_valid_media_sku(sku):
        return Rejected(RejectionCode.INVALID_SKU, sku)
    if not media_ref:
        return Rejected(RejectionCode.NO_MEDIA, sku)

    existing = db.query(models.Product).filter(models.Product.sku == sku).first()
    if existing and existing.factory_id != factory.id:
        return Rejected(RejectionCode.SKU_TAKEN, sku)
    return Submission(
        sku=sku, title=title, price=price, media_ref=media_ref,
        exists=bool(existing) or media.has_media(sku),
    )


# --- سؤال التحديث (المنتج موجود) ---

def request_update_confirmation(
    db: Session, factory: models.Factory, channel: str, submission: Submission
) -> models.PendingMediaUpdate:
    # طلب جديد لنفس الرمز يلغي القديم
    db.query(models.PendingMediaUpdate).filter(
        models.PendingMediaUpdate.factory_id == factory.id,
        models.PendingMediaUpdate.channel == channel,
        models.PendingMediaUpdate.sku == submission.sku,
    ).delete(synchronize_session=False)
    pending = models.PendingMediaUpdate(
        factory_id=factory.id, channel=channel, sku=submission.sku, title=submission.title,
        price=submission.price, media_ref=submission.media_ref,
    )
    db.add(pending)
    db.commit()
    db.refresh(pending)
    return pending


def find_pending(
    db: Session, factory: models.Factory, channel: str, pending_id: Optional[int] = None
) -> Optional[models.PendingMediaUpdate]:
    """الطلب المعلّق غير المنتهي لهذا المصنع في هذه القناة بالذات (آخرها إن لم يُحدَّد المعرّف).
    اشتراط المصنع والقناة يمنع الرد على طلب غيرك."""
    query = db.query(models.PendingMediaUpdate).filter(
        models.PendingMediaUpdate.factory_id == factory.id,
        models.PendingMediaUpdate.channel == channel,
        models.PendingMediaUpdate.created_at >= datetime.utcnow() - PENDING_TTL,
    )
    if pending_id is not None:
        query = query.filter(models.PendingMediaUpdate.id == pending_id)
    return query.order_by(models.PendingMediaUpdate.id.desc()).first()


def consume_pending(db: Session, pending: models.PendingMediaUpdate) -> Submission:
    """يحذف الطلب (فلا يؤدي ضغطان متتاليان إلى تحديثين) ويُرجع ما كان ينتظر التنفيذ."""
    submission = Submission(
        sku=pending.sku, title=pending.title, price=pending.price,
        media_ref=pending.media_ref, exists=True,
    )
    db.delete(pending)
    db.commit()
    return submission


# --- الحفظ ---

class StoreStatus(str, Enum):
    STORED = "stored"
    DOWNLOAD_FAILED = "download_failed"
    NOT_IMAGE = "not_image"
    SKU_TAKEN = "sku_taken"
    SAVE_FAILED = "save_failed"


@dataclass
class StoreResult:
    status: StoreStatus
    product: Optional[models.Product] = None
    created: bool = False         # True = منتج جديد، False = تحديث
    synced: bool = False          # نجحت مزامنة كتالوج ميتا


async def store_product(
    db: Session, factory: models.Factory, submission: Submission, content: Optional[bytes]
) -> StoreResult:
    """يحفظ الصورة في مجلد المنتج، وينشئ المنتج أو يحدّثه، ثم يزامن ميتا.
    content: بايتات الصورة كما نزّلتها القناة (None = فشل التنزيل)."""
    if not content:
        return StoreResult(StoreStatus.DOWNLOAD_FAILED)

    # الرابط يحمل الامتداد الفعلي، فنحدده من محتوى الصورة قبل بنائه
    ext = media.detect_image_extension(content)
    if ext is None:
        return StoreResult(StoreStatus.NOT_IMAGE)
    fields = {
        "sku": submission.sku, "title": submission.title, "price": submission.price,
        "primary_media_url": media.public_image_url(submission.sku, ext),
    }
    try:
        product, outcome = upsert_product(db, factory, fields)
    except SkuOwnershipError:
        db.rollback()
        return StoreResult(StoreStatus.SKU_TAKEN)

    # الملف قبل commit: إن فشلت الكتابة نتراجع عن القاعدة فلا يبقى منتج بلا صورة
    try:
        media.save_main_image(submission.sku, content)
    except (ValueError, OSError):
        db.rollback()
        logger.exception("Failed saving image for %s", submission.sku)
        return StoreResult(StoreStatus.SAVE_FAILED)
    db.commit()
    db.refresh(product)

    # نزامن دائماً، حتى لو لم تتغير بيانات النص، لأن الصورة نفسها تغيّرت
    result = await sync_product_to_meta(product.id, db)
    db.refresh(product)
    return StoreResult(
        StoreStatus.STORED, product=product, created=outcome == "created",
        synced=result.get("status") == "success",
    )
