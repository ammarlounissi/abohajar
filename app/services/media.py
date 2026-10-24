"""حفظ صور المنتجات على القرص.

البنية:  {MEDIA_ROOT}/{الفئة}/{SKU}/{الرقم}.{الامتداد}      مثل  B/B001/1.jpg
الفئة = الحروف في بداية الـ SKU (B001 -> B، PTK012 -> PTK).

موقع الوسائط يُحسب من المصنع صاحب المنتج (factory_id):
  - مصنع صاحب المشروع (DEFAULT_FACTORY_ID) أو factory_id=None: المسار والرابط القديمان كما هما حرفياً
        القرص  {MEDIA_ROOT}/{الفئة}/{SKU}/        الرابط  {IMAGE_BASE_URL}/{SKU}/{n}.{ext}   (يخدمه Nginx)
  - أي مصنع آخر:
        القرص  {FACTORY_MEDIA_ROOT}/{factory_id}/{الفئة}/{SKU}/
        الرابط {BASE_URL}/api/media/factories/{factory_id}/{SKU}/{n}.{ext}                  (يخدمه FastAPI)
    (لاحقاً: مصنع بدومين خاص يخدمه Nginx، انظر ARCHITECTURE.md)
إن لم يُضبط DEFAULT_FACTORY_ID فكل المصانع تعامل بالسلوك القديم.

الرابط العام قصير:  {IMAGE_BASE_URL}/{SKU}/{الرقم}.{الامتداد}     مثل  https://hadjretbladi.com/B001/22.jpg
(Nginx يحوّله إلى {MEDIA_ROOT}/B/B001/22.jpg). شكل الرابط يُبنى في دالة واحدة هي public_image_url،
وكتالوج ميتا يعتمد عليه، فلا يُبنى في مكان آخر.
"""
import logging
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# fullmatch وليس match: لأن $ تقبل سطراً جديداً في النهاية
_SKU_PATH_RE = re.compile(r"([A-Z]+)[0-9]+")

# الامتدادات المقبولة كصور
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
MAIN_IMAGE_INDEX = 1


class InvalidSkuError(ValueError):
    """الـ SKU لا يصلح لبناء مسار على القرص."""


def category_of(sku: str) -> str:
    match = _SKU_PATH_RE.fullmatch(sku or "")
    if not match:
        raise InvalidSkuError(sku)
    return match.group(1)


def is_valid_media_sku(sku: str) -> bool:
    return bool(_SKU_PATH_RE.fullmatch(sku or ""))


def is_owner_factory(factory_id: Optional[int]) -> bool:
    """مصنع صاحب المشروع (يحتفظ بالمسارات والروابط القصيرة القديمة)."""
    default = settings.DEFAULT_FACTORY_ID
    return factory_id is None or default is None or factory_id == default


def media_root_for(factory_id: Optional[int] = None) -> Path:
    """جذر وسائط المصنع: MEDIA_ROOT لصاحب المشروع، وإلا FACTORY_MEDIA_ROOT/{factory_id}."""
    if is_owner_factory(factory_id):
        return settings.MEDIA_ROOT
    return settings.FACTORY_MEDIA_ROOT / str(int(factory_id))


def product_dir(sku: str, factory_id: Optional[int] = None) -> Path:
    """مجلد المنتج {جذر المصنع}/{CATEGORY}/{SKU}. لا ينشئه."""
    category = category_of(sku)
    root = media_root_for(factory_id).resolve()
    path = (root / category / sku).resolve()
    if root not in path.parents:          # حماية إضافية من path traversal
        raise InvalidSkuError(sku)
    return path


def public_media_url(sku: str, filename: str, factory_id: Optional[int] = None) -> str:
    """الرابط العام لملف داخل مجلد المنتج (صورة أو فيديو). الشكل يُبنى هنا فقط."""
    if is_owner_factory(factory_id):
        return f"{settings.IMAGE_BASE_URL}/{sku}/{filename}"
    return f"{settings.BASE_URL}/api/media/factories/{int(factory_id)}/{sku}/{filename}"


def public_image_url(sku: str, ext: str, index: int = MAIN_IMAGE_INDEX, factory_id: Optional[int] = None) -> str:
    """ext بنقطة كما في الملف الفعلي، مثل '.jpg'."""
    return public_media_url(sku, f"{index}{ext}", factory_id)


def existing_image_ext(sku: str, index: int = MAIN_IMAGE_INDEX, factory_id: Optional[int] = None) -> Optional[str]:
    """امتداد الصورة الموجودة فعلاً على القرص (كما كُتب في اسم الملف)، أو None."""
    folder = product_dir(sku, factory_id)
    if not folder.is_dir():
        return None
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.stem == str(index) and f.suffix.lower() in IMAGE_EXTENSIONS:
            return f.suffix
    return None


def _is_real_file(path: Path) -> bool:
    # نتجاهل ملفات Syncthing والملفات المؤقتة ونظام التشغيل
    return not path.name.startswith(".") and path.suffix != ".tmp"


def has_media(sku: str, factory_id: Optional[int] = None) -> bool:
    """هل لمجلد المنتج وجود فعلي (مجلد وفيه ملف واحد على الأقل)؟"""
    folder = product_dir(sku, factory_id)
    return folder.is_dir() and any(_is_real_file(p) for p in folder.iterdir())


def detect_image_extension(content: bytes) -> Optional[str]:
    """نحدد النوع من محتوى الملف نفسه، لا من الـ mime الذي يرسله المرسل."""
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    return None


def _archive_existing(sku: str, folder: Path, index: int) -> bool:
    """ننقل الصورة القديمة إلى MEDIA_BACKUP_DIR بدل حذفها."""
    archived = False
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for old in sorted(folder.iterdir()):
        # بغض النظر عن حالة الأحرف (1.JPG) كي لا يبقى ملفان لنفس الرقم
        if old.is_file() and old.stem == str(index) and old.suffix.lower() in IMAGE_EXTENSIONS:
            backup_dir = settings.MEDIA_BACKUP_DIR / sku
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(backup_dir / f"{index}_{stamp}{old.suffix}"))
            archived = True
    return archived


def save_main_image(sku: str, content: bytes, factory_id: Optional[int] = None) -> bool:
    """يحفظ الصورة الرئيسية (1.ext) في مجلد المنتج وينشئ المجلد إن لزم.
    تُعيد True إن استبدلت صورة قديمة (وهذه تُنقل إلى النسخة الاحتياطية).
    ترفع InvalidSkuError لـ SKU غير صالح، وValueError لمحتوى ليس صورة معروفة."""
    ext = detect_image_extension(content)
    if ext is None:
        raise ValueError("الملف ليس صورة JPEG أو PNG أو WebP")

    folder = product_dir(sku, factory_id)
    folder.mkdir(parents=True, exist_ok=True, mode=0o775)

    # نكتب في ملف مؤقت ثم نستبدل، فلا يرى Nginx ولا Syncthing ملفاً ناقصاً
    tmp = folder / f".upload-{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(content)
        replaced = _archive_existing(sku, folder, MAIN_IMAGE_INDEX)
        os.replace(tmp, folder / f"{MAIN_IMAGE_INDEX}{ext}")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("Saved main image for %s (%s, replaced=%s)", sku, ext, replaced)
    return replaced
