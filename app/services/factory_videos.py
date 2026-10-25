"""توليد فيديوهات المصنع من صور منتجاته أو مكتبة وسائطه."""
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote, urlsplit

from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.services import media, media_library, video_maker
from app.services.errors import InvalidInputError, NotFoundError
from app.services.factories import get_factory


@dataclass
class FactoryVideoResult:
    factory_id: int
    source: str
    sku: Optional[str]
    url: str
    size_bytes: int
    duration: float
    images_used: int
    audio: Optional[str]


def _product_images(product: models.Product) -> List[Path]:
    try:
        return video_maker.list_source_images(product.sku, product.factory_id)
    except media.InvalidSkuError:
        return []


def _library_images(db: Session, factory_id: int) -> List[Path]:
    prefix = f"/api/media/factories/{factory_id}/"
    images = set()
    entries = db.query(models.Media).filter(
        models.Media.factory_id == factory_id,
        models.Media.media_type == "image",
    ).all()
    for entry in entries:
        path = urlsplit(entry.url).path
        if not path.startswith(prefix):
            continue
        filename = path[len(prefix):]
        if "/" in filename or Path(filename).suffix.lower() not in media.IMAGE_EXTENSIONS:
            continue
        local_path = media_library.factory_media_file_path(factory_id, filename)
        if local_path is not None and local_path.is_file():
            images.add(local_path)
    return sorted(images)


def _audio_selection(audio: Optional[str]) -> Optional[str]:
    if audio:
        video_maker.resolve_audio(audio)
        return audio
    files = video_maker.list_audio_files()
    return files[0] if files else None


def get_video_options(db: Session, factory_id: int) -> dict:
    get_factory(db, factory_id)
    products = db.query(models.Product).filter(
        models.Product.factory_id == factory_id,
    ).order_by(models.Product.sku).all()
    return {
        "factory_id": factory_id,
        "products": [
            {"sku": product.sku, "title": product.title}
            for product in products if _product_images(product)
        ],
        "audio_files": video_maker.list_audio_files(),
        "default_audio": _audio_selection(None),
        "library_images_count": len(_library_images(db, factory_id)),
        "max_duration": video_maker.MAX_FACTORY_DURATION,
    }


def generate_factory_video(
    db: Session,
    factory_id: int,
    *,
    source: str,
    sku: Optional[str],
    duration: float,
    audio: Optional[str],
) -> FactoryVideoResult:
    get_factory(db, factory_id)
    if not video_maker.MIN_DURATION <= duration <= video_maker.MAX_FACTORY_DURATION:
        raise InvalidInputError("مدة الفيديو يجب أن تكون بين 3 و60 ثانية")
    selected_audio = _audio_selection(audio)

    if source in ("specific_product", "random_product"):
        query = db.query(models.Product).filter(models.Product.factory_id == factory_id)
        if source == "specific_product":
            if not sku:
                raise InvalidInputError("رمز SKU مطلوب عند اختيار منتج محدد")
            product = query.filter(models.Product.sku == sku).first()
            if product is None:
                raise NotFoundError("المنتج غير موجود ضمن منتجات هذا المصنع")
            if not _product_images(product):
                raise NotFoundError("لا توجد صور لهذا المنتج لبناء فيديو منها")
        else:
            products = [item for item in query.all() if _product_images(item)]
            if not products:
                raise NotFoundError("لا توجد منتجات لهذا المصنع تحتوي على صور")
            product = random.choice(products)

        result = video_maker.create_factory_product_video(
            product.sku, duration=duration, audio=selected_audio, factory_id=factory_id,
        )
        try:
            product.video_url = result.url
            db.commit()
        except Exception:
            db.rollback()
            result.path.unlink(missing_ok=True)
            raise
        return FactoryVideoResult(
            factory_id=factory_id, source=source, sku=product.sku, url=result.url,
            size_bytes=result.size_bytes, duration=result.duration,
            images_used=result.images_used, audio=result.audio,
        )

    if source != "library":
        raise InvalidInputError("مصدر الصور غير معروف")
    images = _library_images(db, factory_id)
    if not images:
        raise NotFoundError("لا توجد صور محلية في مكتبة هذا المصنع")
    selected_images = random.sample(images, min(len(images), video_maker.MAX_IMAGES))
    directory = media_library.library_dir(factory_id)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.mp4"
    target = directory / filename
    temporary = directory / f".video-{uuid.uuid4().hex}.tmp"
    audio_path = video_maker.resolve_audio(selected_audio) if selected_audio else None
    try:
        size = video_maker.render_video_file(
            selected_images, temporary, duration=duration, audio=audio_path,
            max_bytes=media_library.MAX_UPLOAD_BYTES,
        )
        target.hardlink_to(temporary)
        target.chmod(0o664)
        item = models.Media(
            factory_id=factory_id,
            media_type="video",
            url=f"{settings.BASE_URL}/api/media/factories/{factory_id}/{quote(filename)}",
            description="فيديو مولّد تلقائياً من صور مكتبة المصنع",
        )
        db.add(item)
        db.commit()
    except Exception:
        db.rollback()
        target.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)

    return FactoryVideoResult(
        factory_id=factory_id, source=source, sku=None, url=item.url,
        size_bytes=size, duration=duration, images_used=len(selected_images),
        audio=selected_audio,
    )
