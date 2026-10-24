import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services import media
from app.services.media_library import MEDIA_EXTENSIONS, factory_media_file_path
from app.services.instagram_media import derivative_path

router = APIRouter(prefix="/api/media", tags=["public-media"])


@router.get("/instagram/{filename}", include_in_schema=False)
def get_instagram_image(filename: str):
    path = derivative_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="الصورة غير موجودة")
    return FileResponse(
        path,
        media_type="video/mp4" if path.suffix == ".mp4" else "image/jpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


_PRODUCT_FILE = re.compile(r"[0-9]+\.(jpg|jpeg|png|webp|mp4)")
_PRODUCT_FILE_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".mp4": "video/mp4",
}


@router.get("/factories/{factory_id}/{sku}/{filename}", include_in_schema=False)
def get_factory_product_media(factory_id: int, sku: str, filename: str):
    """صور وفيديوهات منتجات المصانع غير صاحب المشروع (من FACTORY_MEDIA_ROOT/{id}/{الفئة}/{SKU}/).
    منتجات صاحب المشروع يخدمها Nginx مباشرة بالروابط القصيرة، فلا تُخدم من هنا."""
    if media.is_owner_factory(factory_id) or not _PRODUCT_FILE.fullmatch(filename or ""):
        raise HTTPException(status_code=404, detail="الوسيط غير موجود")
    try:
        folder = media.product_dir(sku, factory_id)
    except media.InvalidSkuError:
        raise HTTPException(status_code=404, detail="الوسيط غير موجود")
    path = (folder / filename).resolve()
    if path.parent != folder or not path.is_file():
        raise HTTPException(status_code=404, detail="الوسيط غير موجود")
    return FileResponse(
        path,
        media_type=_PRODUCT_FILE_TYPES[path.suffix.lower()],
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/factories/{factory_id}/{filename}", include_in_schema=False)
def get_factory_media(factory_id: int, filename: str):
    path = factory_media_file_path(factory_id, filename)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="الوسيط غير موجود")
    extension = path.suffix.lower()
    content_type = MEDIA_EXTENSIONS[extension][1]
    headers = {
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    }
    if extension == ".svg":
        headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
    return FileResponse(
        path,
        media_type=content_type,
        headers=headers,
    )
