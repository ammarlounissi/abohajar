"""حفظ صور المنتجات على القرص.

البنية:  {MEDIA_ROOT}/{الفئة}/{SKU}/{الرقم}.{الامتداد}      مثل  B/B001/1.jpg
الفئة = الحروف في بداية الـ SKU (B001 -> B، PTK012 -> PTK).

الرابط العام بلا امتداد (…/products/B/B001/1) وتحلّه Nginx بـ try_files،
ولا يجوز تغيير شكله لأن كتالوج ميتا يعتمد عليه.
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

# نفس الامتدادات التي تجربها Nginx في try_files
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


def product_dir(sku: str) -> Path:
    """مجلد المنتج {MEDIA_ROOT}/{CATEGORY}/{SKU}. لا ينشئه."""
    category = category_of(sku)
    root = settings.MEDIA_ROOT.resolve()
    path = (root / category / sku).resolve()
    if root not in path.parents:          # حماية إضافية من path traversal
        raise InvalidSkuError(sku)
    return path


def public_image_url(sku: str, index: int = MAIN_IMAGE_INDEX) -> str:
    return f"{settings.IMAGE_BASE_URL}/{category_of(sku)}/{sku}/{index}"


def _is_real_file(path: Path) -> bool:
    # نتجاهل ملفات Syncthing والملفات المؤقتة ونظام التشغيل
    return not path.name.startswith(".") and path.suffix != ".tmp"


def has_media(sku: str) -> bool:
    """هل لمجلد المنتج وجود فعلي (مجلد وفيه ملف واحد على الأقل)؟"""
    folder = product_dir(sku)
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
    for ext in IMAGE_EXTENSIONS:
        old = folder / f"{index}{ext}"
        if old.is_file():
            backup_dir = settings.MEDIA_BACKUP_DIR / sku
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(backup_dir / f"{index}_{stamp}{ext}"))
            archived = True
    return archived


def save_main_image(sku: str, content: bytes) -> bool:
    """يحفظ الصورة الرئيسية (1.ext) في مجلد المنتج وينشئ المجلد إن لزم.
    تُعيد True إن استبدلت صورة قديمة (وهذه تُنقل إلى النسخة الاحتياطية).
    ترفع InvalidSkuError لـ SKU غير صالح، وValueError لمحتوى ليس صورة معروفة."""
    ext = detect_image_extension(content)
    if ext is None:
        raise ValueError("الملف ليس صورة JPEG أو PNG أو WebP")

    folder = product_dir(sku)
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
