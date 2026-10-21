from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

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
