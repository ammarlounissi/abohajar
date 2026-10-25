from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app import schemas
from app.core.database import get_db
from app.core.security import require_admin
from app.services import factories as factory_service
from app.services import factory_videos
from app.services import media_library

router = APIRouter(prefix="/api/factories", tags=["factories"], dependencies=[Depends(require_admin)])


@router.post("/", response_model=schemas.FactoryResponse, status_code=status.HTTP_201_CREATED)
def create_factory(factory: schemas.FactoryCreate, db: Session = Depends(get_db)):
    return factory_service.create_factory(db, **factory.model_dump())


@router.get("/", response_model=List[schemas.FactoryResponse])
def get_factories(db: Session = Depends(get_db)):
    return factory_service.list_factories(db)


@router.get("/{factory_id}", response_model=schemas.FactoryResponse)
def get_factory(factory_id: int, db: Session = Depends(get_db)):
    return factory_service.get_factory(db, factory_id)


@router.patch("/{factory_id}", response_model=schemas.FactoryResponse)
def update_factory(factory_id: int, update: schemas.FactoryUpdate, db: Session = Depends(get_db)):
    return factory_service.update_factory(db, factory_id, update.model_dump(exclude_unset=True))


@router.delete("/{factory_id}")
def delete_factory(factory_id: int, db: Session = Depends(get_db)):
    factory_service.delete_factory(db, factory_id)
    return {"status": "success", "message": "تم حذف المصنع بنجاح"}


# --- جهات اتصال المصنع في القنوات (واتساب/تيليجرام/بريد) ---

@router.get("/{factory_id}/contacts", response_model=List[schemas.FactoryContactResponse])
def get_contacts(factory_id: int, db: Session = Depends(get_db)):
    return factory_service.list_contacts(db, factory_id)


@router.post("/{factory_id}/contacts", response_model=schemas.FactoryContactResponse,
             status_code=status.HTTP_201_CREATED)
def add_contact(factory_id: int, contact: schemas.FactoryContactCreate, db: Session = Depends(get_db)):
    return factory_service.add_contact(db, factory_id, contact.channel.value, contact.identifier)


@router.delete("/{factory_id}/contacts/{contact_id}")
def remove_contact(factory_id: int, contact_id: int, db: Session = Depends(get_db)):
    factory_service.remove_contact(db, factory_id, contact_id)
    return {"status": "success", "message": "تم حذف جهة الاتصال"}


# --- مكتبة وسائط المصنع ---

@router.get("/{factory_id}/media", response_model=List[schemas.FactoryMediaResponse])
def get_factory_media(
    factory_id: int,
    media_type: Optional[str] = Query(None, pattern="^(image|video)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return media_library.list_factory_media(
        db, factory_id, media_type=media_type, skip=skip, limit=limit,
    )


@router.post(
    "/{factory_id}/media",
    response_model=schemas.FactoryMediaResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_factory_media(
    factory_id: int, item: schemas.FactoryMediaCreate, db: Session = Depends(get_db),
):
    return media_library.add_factory_media(
        db, factory_id, media_type=item.media_type, url=str(item.url), description=item.description,
    )


@router.post(
    "/{factory_id}/media/upload",
    response_model=schemas.FactoryMediaResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_factory_media(
    factory_id: int,
    file: UploadFile = File(...),
    description: Optional[str] = Form(None, max_length=2000),
    db: Session = Depends(get_db),
):
    try:
        return media_library.upload_factory_media(
            db,
            factory_id,
            file=file.file,
            filename=file.filename,
            content_type=file.content_type,
            description=description,
        )
    finally:
        file.file.close()


@router.delete("/{factory_id}/media/{media_id}")
def delete_factory_media(factory_id: int, media_id: int, db: Session = Depends(get_db)):
    media_library.delete_factory_media(db, factory_id, media_id)
    return {"status": "success", "message": "تم حذف الوسيط من مكتبة المصنع"}


@router.get("/{factory_id}/videos/options", response_model=schemas.FactoryVideoOptionsResponse)
def get_factory_video_options(factory_id: int, db: Session = Depends(get_db)):
    """خيارات توليد فيديو المصنع: المنتجات ذات الصور، الأصوات، وصور المكتبة المتاحة."""
    return factory_videos.get_video_options(db, factory_id)


@router.post(
    "/{factory_id}/videos/generate",
    response_model=schemas.FactoryVideoResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_factory_video(
    factory_id: int,
    request: schemas.FactoryVideoGenerateRequest,
    db: Session = Depends(get_db),
):
    """ينشئ فيديو حتى دقيقة بخلفية مموهة، ويحفظه مع المنتج أو في مكتبة المصنع."""
    result = factory_videos.generate_factory_video(
        db,
        factory_id,
        source=request.source,
        sku=request.sku,
        template_id=request.template,
        duration=request.duration,
        audio=request.audio,
    )
    return schemas.FactoryVideoResponse(
        factory_id=result.factory_id,
        source=result.source,
        template=result.template,
        sku=result.sku,
        video_url=result.url,
        size_bytes=result.size_bytes,
        duration=result.duration,
        images_used=result.images_used,
        audio=result.audio,
    )
