"""حفظ صور المنتجات على القرص.

البنية:  {MEDIA_ROOT}/{الفئة}/{SKU}/{الرقم}.{الامتداد}      مثل  B/B001/1.jpg
الفئة = الحروف في بداية الـ SKU (B001 -> B، PTK012 -> PTK).

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
from typing import List, Optional, Tuple

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


def product_dir(sku: str) -> Path:
    """مجلد المنتج {MEDIA_ROOT}/{CATEGORY}/{SKU}. لا ينشئه."""
    category = category_of(sku)
    root = settings.MEDIA_ROOT.resolve()
    path = (root / category / sku).resolve()
    if root not in path.parents:          # حماية إضافية من path traversal
        raise InvalidSkuError(sku)
    return path


def public_image_url(sku: str, ext: str, index: int = MAIN_IMAGE_INDEX) -> str:
    """ext بنقطة كما في الملف الفعلي، مثل '.jpg'."""
    return f"{settings.IMAGE_BASE_URL}/{sku}/{index}{ext}"


def existing_image_ext(sku: str, index: int = MAIN_IMAGE_INDEX) -> Optional[str]:
    """امتداد الصورة الموجودة فعلاً على القرص (كما كُتب في اسم الملف)، أو None."""
    folder = product_dir(sku)
    if not folder.is_dir():
        return None
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.stem == str(index) and f.suffix.lower() in IMAGE_EXTENSIONS:
            return f.suffix
    return None


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
    for old in sorted(folder.iterdir()):
        # بغض النظر عن حالة الأحرف (1.JPG) كي لا يبقى ملفان لنفس الرقم
        if old.is_file() and old.stem == str(index) and old.suffix.lower() in IMAGE_EXTENSIONS:
            backup_dir = settings.MEDIA_BACKUP_DIR / sku
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(backup_dir / f"{index}_{stamp}{old.suffix}"))
            archived = True
    return archived


def save_image(sku: str, index: int, content: bytes) -> bool:
    """يحفظ الصورة رقم index في مجلد المنتج وينشئ المجلد إن لزم.
    تُعيد True إن استبدلت صورة قديمة بنفس الرقم (وهذه تُنقل إلى النسخة الاحتياطية).
    ترفع InvalidSkuError لـ SKU غير صالح، وValueError لمحتوى ليس صورة معروفة."""
    ext = detect_image_extension(content)
    if ext is None:
        raise ValueError("الملف ليس صورة JPEG أو PNG أو WebP")
    if index < 1:
        raise ValueError("رقم الصورة يبدأ من 1")

    folder = product_dir(sku)
    folder.mkdir(parents=True, exist_ok=True, mode=0o775)

    # نكتب في ملف مؤقت ثم نستبدل، فلا يرى Nginx ولا Syncthing ملفاً ناقصاً
    tmp = folder / f".upload-{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(content)
        replaced = _archive_existing(sku, folder, index)
        os.replace(tmp, folder / f"{index}{ext}")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    logger.info("Saved image %s for %s (%s, replaced=%s)", index, sku, ext, replaced)
    return replaced


def save_main_image(sku: str, content: bytes) -> bool:
    """الصورة الرئيسية = الرقم 1."""
    return save_image(sku, MAIN_IMAGE_INDEX, content)


def list_images(sku: str) -> List[Tuple[int, str]]:
    """صور المنتج الموجودة فعلاً: [(الرقم، الامتداد بنقطة)] مرتبة بالرقم. الصور الرقمية فقط."""
    if not is_valid_media_sku(sku):
        return []                          # رموز قديمة بشرطات (STONE-GLM-01) لا مجلد لها
    folder = product_dir(sku)
    if not folder.is_dir():
        return []
    found = {}
    for f in sorted(folder.iterdir()):
        if (f.is_file() and not f.name.startswith(".") and re.fullmatch(r"[0-9]+", f.stem)
                and f.suffix.lower() in IMAGE_EXTENSIONS and int(f.stem) > 0):
            found.setdefault(int(f.stem), f.suffix)
    return sorted(found.items())


def image_urls(sku: str) -> List[str]:
    return [public_image_url(sku, ext, i) for i, ext in list_images(sku)]


def delete_image(sku: str, index: int) -> bool:
    """يحذف الصورة رقم index بنقلها إلى النسخة الاحتياطية (لا حذف نهائي)، ثم يعيد الترقيم
    كي تبقى الأرقام متسلسلة من 1. تُعيد False إن لم توجد الصورة."""
    folder = product_dir(sku)
    images = list_images(sku)
    if index not in {i for i, _ in images}:
        return False
    _archive_existing(sku, folder, index)
    for i, ext in images:
        if i > index:
            os.replace(folder / f"{i}{ext}", folder / f"{i - 1}{ext}")
    logger.info("Deleted image %s of %s (archived, renumbered)", index, sku)
    return True


# --- الفيديو القصير: ملف واحد باسم video.mp4 (اسمه غير رقمي فيتجاهله فاحص الاستيراد) ---
VIDEO_STEM = "video"
VIDEO_EXTENSIONS = (".mp4",)


def detect_video_extension(content: bytes) -> Optional[str]:
    """MP4 يحمل 'ftyp' في البايتات 4-8. نحدد النوع من المحتوى لا من اسم الملف."""
    if len(content) > 12 and content[4:8] == b"ftyp":
        return ".mp4"
    return None


def existing_video_ext(sku: str) -> Optional[str]:
    if not is_valid_media_sku(sku):
        return None
    folder = product_dir(sku)
    for ext in VIDEO_EXTENSIONS:
        if (folder / f"{VIDEO_STEM}{ext}").is_file():
            return ext
    return None


def video_url(sku: str) -> Optional[str]:
    ext = existing_video_ext(sku)
    return f"{settings.IMAGE_BASE_URL}/{sku}/{VIDEO_STEM}{ext}" if ext else None


def _archive_video(sku: str, folder: Path) -> bool:
    archived = False
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for ext in VIDEO_EXTENSIONS:
        old = folder / f"{VIDEO_STEM}{ext}"
        if old.is_file():
            backup_dir = settings.MEDIA_BACKUP_DIR / sku
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old), str(backup_dir / f"{VIDEO_STEM}_{stamp}{ext}"))
            archived = True
    return archived


def save_video(sku: str, content: bytes) -> str:
    """يحفظ الفيديو (ويستبدل القديم بعد نقله إلى النسخة الاحتياطية). تُعيد رابطه العام."""
    ext = detect_video_extension(content)
    if ext is None:
        raise ValueError("الملف ليس فيديو MP4")
    folder = product_dir(sku)
    folder.mkdir(parents=True, exist_ok=True, mode=0o775)
    tmp = folder / f".upload-{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(content)
        _archive_video(sku, folder)
        os.replace(tmp, folder / f"{VIDEO_STEM}{ext}")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return video_url(sku)


def remove_video(sku: str) -> bool:
    """إزالة الفيديو بنقله إلى النسخة الاحتياطية."""
    return _archive_video(sku, product_dir(sku))


def quarantine_product_dir(sku: str) -> Optional[Path]:
    """مجلد منتج لم يكتمل إنشاؤه: ننقله إلى النسخة الاحتياطية (لا حذف) كي لا يحجز الرمز."""
    folder = product_dir(sku)
    if not folder.is_dir():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = settings.MEDIA_BACKUP_DIR / f"{sku}_failed_{stamp}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(dest))
    return dest
