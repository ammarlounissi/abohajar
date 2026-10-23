"""منطق مكتبة روابط الوسائط التابعة للمصانع."""
import re
import uuid
from pathlib import Path
from typing import BinaryIO, List, Optional
from urllib.parse import quote, urlsplit

from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.services.errors import InvalidInputError, NotFoundError
from app.services.factories import get_factory

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MEDIA_EXTENSIONS = {
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".png": ("image", "image/png"),
    ".webp": ("image", "image/webp"),
    ".gif": ("image", "image/gif"),
    ".ico": ("image", "image/x-icon"),
    ".svg": ("image", "image/svg+xml"),
    ".mp4": ("video", "video/mp4"),
    ".mov": ("video", "video/quicktime"),
    ".webm": ("video", "video/webm"),
}
_SAVED_FILENAME = re.compile(
    r"^[0-9a-f]{32}\.(?:jpg|jpeg|png|webp|gif|ico|svg|mp4|mov|webm)$"
)


def list_factory_media(
    db: Session, factory_id: int, *, media_type: Optional[str] = None,
    skip: int = 0, limit: int = 100,
) -> List[models.Media]:
    get_factory(db, factory_id)
    query = db.query(models.Media).filter(models.Media.factory_id == factory_id)
    if media_type:
        query = query.filter(models.Media.media_type == media_type)
    return query.order_by(models.Media.id.desc()).offset(skip).limit(limit).all()


def add_factory_media(
    db: Session, factory_id: int, *, media_type: str, url: str, description: Optional[str],
) -> models.Media:
    factory = get_factory(db, factory_id)
    item = models.Media(
        factory_id=factory.id,
        media_type=media_type,
        url=url,
        description=description,
    )
    db.add(item)
    db.flush()
    db.refresh(item)
    db.commit()
    return item


def factory_media_file_path(factory_id: int, filename: str) -> Optional[Path]:
    """مسار ملف محفوظ، مع منع اجتياز مجلد المصنع."""
    if not _SAVED_FILENAME.fullmatch(filename):
        return None
    path = (settings.FACTORY_MEDIA_ROOT / str(factory_id) / filename).resolve()
    root = settings.FACTORY_MEDIA_ROOT.resolve()
    if path.parent != (root / str(factory_id)).resolve():
        return None
    return path


def upload_factory_media(
    db: Session,
    factory_id: int,
    *,
    file: BinaryIO,
    filename: Optional[str],
    content_type: Optional[str],
    description: Optional[str],
) -> models.Media:
    factory = get_factory(db, factory_id)
    extension = Path(filename or "").suffix.lower()
    media_info = MEDIA_EXTENSIONS.get(extension)
    if media_info is None:
        raise InvalidInputError("امتداد الملف غير مدعوم")
    media_type, expected_content_type = media_info
    accepted_content_types = {expected_content_type}
    if extension == ".ico":
        accepted_content_types.add("image/vnd.microsoft.icon")
    if content_type and content_type not in accepted_content_types:
        raise InvalidInputError("نوع محتوى الملف لا يتوافق مع امتداده")

    directory = settings.FACTORY_MEDIA_ROOT / str(factory.id)
    directory.mkdir(parents=True, exist_ok=True)
    for _ in range(5):
        saved_filename = f"{uuid.uuid4().hex}{extension}"
        path = directory / saved_filename
        try:
            destination = path.open("xb")
            break
        except FileExistsError:
            continue
    else:
        raise OSError("تعذّر إنشاء اسم فريد لملف الوسائط بعد عدة محاولات")

    size = 0
    try:
        with destination:
            while chunk := file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise InvalidInputError("حجم الملف يتجاوز الحد الأقصى (100 ميغابايت)")
                destination.write(chunk)
        if size == 0:
            raise InvalidInputError("لا يمكن رفع ملف فارغ")

        public_url = (
            f"{settings.BASE_URL}/api/media/factories/{factory.id}/{quote(saved_filename)}"
        )
        item = models.Media(
            factory_id=factory.id,
            media_type=media_type,
            url=public_url,
            description=description.strip() if description else None,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise


def delete_factory_media(db: Session, factory_id: int, media_id: int) -> None:
    get_factory(db, factory_id)
    item = db.query(models.Media).filter(
        models.Media.id == media_id,
        models.Media.factory_id == factory_id,
    ).first()
    if item is None:
        raise NotFoundError("الوسيط غير موجود في مكتبة هذا المصنع")
    match = re.fullmatch(
        rf"/api/media/factories/{factory_id}/([0-9a-f]{{32}}\.(?:jpg|jpeg|png|webp|gif|ico|svg|mp4|mov|webm))",
        urlsplit(item.url).path,
    )
    file_path = factory_media_file_path(factory_id, match.group(1)) if match else None
    db.delete(item)
    db.commit()
    if file_path is not None:
        file_path.unlink(missing_ok=True)
