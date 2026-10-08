"""منطق المنتجات المشترك بين رفع CSV ورسائل واتساب."""
import csv
import io
import re
from typing import List, Optional, Tuple

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.config import settings

# فاصل يجب أن يحيط به مسافات (أو سطر جديد) حتى لا ينكسر SKU مثل STONE-GLM-01
_CAPTION_SPLIT = re.compile(r"\s+[-–—|]\s+|\n")

REQUIRED_COLUMNS = {"sku", "title", "price"}
OPTIONAL_COLUMNS = ("description", "currency", "condition", "brand", "availability")


class SkuOwnershipError(Exception):
    """الـ SKU موجود ويتبع مصنعاً آخر."""


def parse_caption(caption: str) -> Optional[Tuple[str, float, str]]:
    """'اسم المنتج - السعر - SKU'  ->  (title, price, sku)  أو None إن كان التنسيق خاطئاً."""
    parts = [p.strip() for p in _CAPTION_SPLIT.split(caption or "") if p.strip()]
    if len(parts) != 3:
        return None
    title, price_text, sku = parts
    try:
        price = float(price_text.replace(",", "."))
    except ValueError:
        return None
    return title, price, sku.upper()


def build_image_url(sku: str) -> str:
    """يبني رابط الصورة الرئيسية من الـ SKU: {IMAGE_BASE_URL}/{الفئة}/{SKU}/1  (بلا امتداد، تحلّه Nginx)
    الفئة هي الحروف في بداية الرمز (B001 -> B، PTK012 -> PTK)."""
    match = re.match(r"[A-Z]+", sku)
    if not settings.IMAGE_BASE_URL or not match:
        return ""
    return f"{settings.IMAGE_BASE_URL}/{match.group(0)}/{sku}/1"


def default_description(title: str, factory: models.Factory) -> str:
    return f"{title} - توريد مباشر من مصنع {factory.name}"


def upsert_product(db: Session, factory: models.Factory, fields: dict) -> Tuple[models.Product, str]:
    """إنشاء المنتج أو تحديث الحقول التي تغيّرت فقط. تُعيد (المنتج، created|updated|unchanged).
    لا تقوم بـ commit، المستدعي هو من يحفظ."""
    existing = db.query(models.Product).filter(models.Product.sku == fields["sku"]).first()

    if existing is None:
        fields = dict(fields)
        if not fields.get("description"):
            fields["description"] = default_description(fields["title"], factory)
        product = models.Product(factory_id=factory.id, **fields)
        db.add(product)
        db.flush()    # الجلسة بدون autoflush، فنضمن رؤية المنتج إن تكرر الـ SKU في الملف
        return product, "created"

    if existing.factory_id != factory.id:
        raise SkuOwnershipError(fields["sku"])

    changed = False
    for key, value in fields.items():
        if key != "sku" and getattr(existing, key) != value:
            setattr(existing, key, value)
            changed = True
    if changed:
        existing.sync_status = models.SyncStatus.PENDING
        return existing, "updated"
    return existing, "unchanged"


def _format_errors(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())


def import_csv(db: Session, factory: models.Factory, content: bytes):
    """معالجة ملف CSV. تُعيد (الملخص، قائمة المنتجات المضافة أو المعدّلة). لا تقوم بـ commit."""
    text = content.decode("utf-8-sig")        # utf-8-sig يتجاوز علامة BOM التي يضيفها Excel
    # Excel بالإعدادات العربية/الفرنسية يحفظ غالباً بفاصلة منقوطة، فنكتشف الفاصل من الترويسة
    header_line = text.splitlines()[0] if text.strip() else ""
    delimiter = max((",", ";", "\t"), key=header_line.count)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("الملف فارغ")

    columns = {c.strip().lower() for c in reader.fieldnames if c}
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise ValueError(f"الملف ينقصه الأعمدة الإلزامية التالية: {', '.join(sorted(missing))}")

    counts = {"created": 0, "updated": 0, "unchanged": 0}
    errors: List[str] = []
    touched: List[models.Product] = []

    for row_num, raw in enumerate(reader, start=2):      # السطر 1 هو الترويسة
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}

        fields = {k: row.get(k, "") for k in ("sku", "title", "price")}
        fields["sku"] = fields["sku"].upper()
        fields["primary_media_url"] = row.get("primary_media_url") or build_image_url(fields["sku"])
        if not fields["primary_media_url"]:
            errors.append(f"السطر {row_num}: لا يوجد رابط صورة، وتعذر بناؤه من الرمز")
            continue
        for key in OPTIONAL_COLUMNS:
            if row.get(key):
                fields[key] = row[key].lower() if key in ("condition", "availability") else row[key]
        if row.get("additional_media_urls"):
            fields["additional_media_urls"] = [
                u.strip() for u in row["additional_media_urls"].split("|") if u.strip()
            ]

        try:
            data = schemas.ProductBase(**fields)
        except ValidationError as exc:
            errors.append(f"السطر {row_num}: {_format_errors(exc)}")
            continue

        # نمرر فقط الحقول الموجودة في الملف حتى لا نكتب فوق قيم لم يرسلها المصنع
        validated = data.model_dump(include=data.model_fields_set)
        try:
            product, outcome = upsert_product(db, factory, validated)
        except SkuOwnershipError:
            errors.append(f"السطر {row_num}: الرمز {data.sku} يتبع مصنعاً آخر")
            continue

        counts[outcome] += 1
        if outcome != "unchanged":
            touched.append(product)

    summary = {
        "total_created": counts["created"],
        "total_updated": counts["updated"],
        "total_unchanged": counts["unchanged"],
        "total_errors": len(errors),
    }
    return {"summary": summary, "errors": errors}, touched
