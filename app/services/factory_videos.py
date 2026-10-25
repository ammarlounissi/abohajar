"""توليد فيديوهات المصنع من صور منتجاته أو مكتبة وسائطه."""
import io
import logging
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote, urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.services import media, media_library, video_maker
from app.services.errors import InvalidInputError, NotFoundError
from app.services.factories import get_factory

logger = logging.getLogger(__name__)
MAX_LIBRARY_IMAGE_BYTES = 20 * 1024 * 1024
MAX_LIBRARY_IMAGE_PIXELS = 50_000_000


@dataclass
class FactoryVideoResult:
    factory_id: int
    source: str
    template: str
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


def _library_image_entries(db: Session, factory_id: int) -> List[models.Media]:
    entries = db.query(models.Media).filter(
        models.Media.factory_id == factory_id,
        models.Media.media_type == "image",
    ).all()
    unique = {}
    for entry in entries:
        parsed = urlsplit(entry.url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            unique.setdefault(entry.url, entry)
    return list(unique.values())


def _local_library_image(entry: models.Media, factory_id: int) -> Optional[Path]:
    prefix = f"/api/media/factories/{factory_id}/"
    path = urlsplit(entry.url).path
    if not path.startswith(prefix):
        return None
    filename = path[len(prefix):]
    if "/" in filename or Path(filename).suffix.lower() not in media.IMAGE_EXTENSIONS:
        return None
    local_path = media_library.factory_media_file_path(factory_id, filename)
    return local_path if local_path is not None and local_path.is_file() else None


def _download_library_image(url: str, destination: Path) -> None:
    """ينزّل صورة مسجلة في المكتبة، ويتحقق منها ويحفظ نسخة JPEG مناسبة لـ ffmpeg."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_LIBRARY_IMAGE_BYTES:
                        raise video_maker.VideoRenderError("حجم إحدى صور المكتبة يتجاوز 20 ميغابايت")
    except video_maker.VideoRenderError:
        raise
    except httpx.HTTPError as exc:
        raise video_maker.VideoRenderError("تعذّر تنزيل صورة مسجلة في مكتبة المصنع") from exc

    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.width * source.height > MAX_LIBRARY_IMAGE_PIXELS:
                raise video_maker.VideoRenderError("أبعاد إحدى صور المكتبة أكبر من الحد المسموح")
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.save(destination, format="JPEG", quality=92)
    except video_maker.VideoRenderError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise video_maker.VideoRenderError("أحد روابط المكتبة لا يحتوي على صورة مدعومة") from exc


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
        "default_template": video_maker.DEFAULT_FACTORY_TEMPLATE,
        "library_images_count": len(_library_image_entries(db, factory_id)),
        "max_duration": video_maker.MAX_FACTORY_DURATION,
        "templates": [
            {
                "id": template.id,
                "name": template.name,
                "duration": template.duration,
                "max_images": template.max_images,
            }
            for template in video_maker.VIDEO_TEMPLATES
        ],
    }


def validate_factory_video_request(
    db: Session, factory_id: int, *, source: str, sku: Optional[str],
    template_id: str, duration: Optional[float], audio: Optional[str],
) -> tuple:
    get_factory(db, factory_id)
    template = video_maker.get_video_template(template_id)
    selected_duration = duration if duration is not None else template.duration
    if not video_maker.MIN_DURATION <= selected_duration <= video_maker.MAX_FACTORY_DURATION:
        raise InvalidInputError("مدة الفيديو يجب أن تكون بين 3 و60 ثانية")
    selected_audio = _audio_selection(audio)

    if source == "specific_product":
        if not sku:
            raise InvalidInputError("رمز SKU مطلوب عند اختيار منتج محدد")
        product = db.query(models.Product).filter(
            models.Product.factory_id == factory_id,
            models.Product.sku == sku,
        ).first()
        if product is None:
            raise NotFoundError("المنتج غير موجود ضمن منتجات هذا المصنع")
        if not _product_images(product):
            raise NotFoundError("لا توجد صور لهذا المنتج لبناء فيديو منها")
    elif source == "random_product":
        products = db.query(models.Product).filter(
            models.Product.factory_id == factory_id,
        ).all()
        if not any(_product_images(product) for product in products):
            raise NotFoundError("لا توجد منتجات لهذا المصنع تحتوي على صور")
    elif source == "library":
        if not _library_image_entries(db, factory_id):
            raise NotFoundError("لا توجد صور مسجلة في مكتبة هذا المصنع")
    else:
        raise InvalidInputError("مصدر الصور غير معروف")
    return template, selected_duration, selected_audio


def generate_factory_video(
    db: Session,
    factory_id: int,
    *,
    source: str,
    sku: Optional[str],
    template_id: str,
    duration: Optional[float],
    audio: Optional[str],
) -> FactoryVideoResult:
    template, selected_duration, selected_audio = validate_factory_video_request(
        db, factory_id, source=source, sku=sku, template_id=template_id,
        duration=duration, audio=audio,
    )

    if source in ("specific_product", "random_product"):
        query = db.query(models.Product).filter(models.Product.factory_id == factory_id)
        if source == "specific_product":
            product = query.filter(models.Product.sku == sku).first()
        else:
            products = [item for item in query.all() if _product_images(item)]
            product = random.choice(products)

        result = video_maker.create_factory_product_video(
            product.sku, duration=selected_duration, audio=selected_audio,
            factory_id=factory_id, template=template,
        )
        try:
            product.video_url = result.url
            db.commit()
        except Exception:
            db.rollback()
            result.path.unlink(missing_ok=True)
            raise
        return FactoryVideoResult(
            factory_id=factory_id, source=source, template=template.id,
            sku=product.sku, url=result.url,
            size_bytes=result.size_bytes, duration=result.duration,
            images_used=result.images_used, audio=result.audio,
        )

    if source != "library":
        raise InvalidInputError("مصدر الصور غير معروف")
    image_entries = _library_image_entries(db, factory_id)
    selected_entries = random.sample(image_entries, len(image_entries))
    directory = media_library.library_dir(factory_id)
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.mp4"
    target = directory / filename
    temporary = directory / f".video-{uuid.uuid4().hex}.tmp"
    audio_path = video_maker.resolve_audio(selected_audio) if selected_audio else None
    downloaded_images = []
    selected_images = []
    try:
        for entry in selected_entries:
            if len(selected_images) >= template.max_images:
                break
            local_path = _local_library_image(entry, factory_id)
            if local_path is not None:
                selected_images.append(local_path)
                continue
            image_path = directory / f".image-{uuid.uuid4().hex}.jpg"
            try:
                _download_library_image(entry.url, image_path)
            except video_maker.VideoRenderError as exc:
                logger.warning(
                    "Skipping unusable factory library image (factory_id=%s, media_id=%s): %s",
                    factory_id, entry.id, exc,
                )
                image_path.unlink(missing_ok=True)
                continue
            downloaded_images.append(image_path)
            selected_images.append(image_path)

        if not selected_images:
            raise video_maker.VideoRenderError(
                "تعذّر استخدام الصور المسجلة في مكتبة المصنع؛ تحقق من الروابط وملفات الصور"
            )
        size = video_maker.render_video_file(
            selected_images, temporary, duration=selected_duration, audio=audio_path,
            max_bytes=media_library.MAX_UPLOAD_BYTES, template=template,
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
        for image_path in downloaded_images:
            image_path.unlink(missing_ok=True)

    return FactoryVideoResult(
        factory_id=factory_id, source=source, template=template.id,
        sku=None, url=item.url, size_bytes=size,
        duration=selected_duration, images_used=len(selected_images),
        audio=selected_audio,
    )
