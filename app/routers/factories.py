from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app import schemas
from app.core.database import get_db
from app.core.security import require_admin
from app.services import factories as factory_service
from app.services import drive_sync
from app.services import factory_reel_requests
from app.services import factory_video_jobs
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
    response_model=schemas.FactoryVideoJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_factory_video(
    factory_id: int,
    request: schemas.FactoryVideoGenerateRequest,
    db: Session = Depends(get_db),
):
    """يضع توليد الفيديو في الطابور ويعيد معرّف المهمة فوراً."""
    job = factory_video_jobs.enqueue_video_job(
        db,
        factory_id,
        source=request.source,
        sku=request.sku,
        template=request.template,
        duration=request.duration,
        audio=request.audio,
    )
    return schemas.FactoryVideoJobAccepted(
        id=job.id,
        factory_id=job.factory_id,
        status="queued",
        status_url=f"/api/factories/{factory_id}/videos/jobs/{job.id}",
    )


@router.post(
    "/{factory_id}/videos/generate-random",
    response_model=schemas.FactoryVideoJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def generate_random_factory_library_video(
    factory_id: int, db: Session = Depends(get_db),
):
    """يضع فيديو مكتبة آلياً لمدة 30 ثانية بصوت وقالب عشوائيين في الطابور."""
    job = factory_video_jobs.enqueue_automated_library_video(db, factory_id)
    return schemas.FactoryVideoJobAccepted(
        id=job.id,
        factory_id=job.factory_id,
        status="queued",
        status_url=f"/api/factories/{factory_id}/videos/jobs/{job.id}",
    )


@router.get(
    "/{factory_id}/videos/jobs/{job_id}",
    response_model=schemas.FactoryVideoJobResponse,
)
def get_factory_video_job(factory_id: int, job_id: int, db: Session = Depends(get_db)):
    """يعرض حالة مهمة توليد الفيديو ورابطه عند اكتمالها."""
    return factory_video_jobs.get_video_job(db, factory_id, job_id)


@router.post(
    "/{factory_id}/reel-post",
    response_model=schemas.FactoryReelRequestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_factory_reel_post(factory_id: int, db: Session = Depends(get_db)):
    """بمعرّف المصنع فقط: يضع في الطابور فيديو مكتبة عشوائياً ووصف Ollama، وعند اكتمالهما ينشئ
    منشور ريل معتمداً جاهزاً للنشر (الوسائط + الكابشن + قنوات المصنع). تابعه بمسار الحالة."""
    request = factory_reel_requests.enqueue_reel_request(db, factory_id)
    return schemas.FactoryReelRequestAccepted(
        id=request.id, factory_id=factory_id, video_job_id=request.video_job_id,
        chat_job_id=request.chat_job_id, status="pending",
        status_url=f"/api/factories/{factory_id}/reel-post/{request.id}",
    )


@router.get("/{factory_id}/reel-post/{request_id}", response_model=schemas.FactoryReelRequestResponse)
def get_factory_reel_post(factory_id: int, request_id: int, db: Session = Depends(get_db)):
    """حالة الطلب. عند succeeded يوجد post_id (المنشور المعتمد)، وعند failed يظهر السبب في error."""
    request = factory_reel_requests.get_reel_request(db, factory_id, request_id)
    return factory_reel_requests.finalize_request(db, request.id)


@router.post("/{factory_id}/drive-sync", response_model=schemas.DriveSyncResponse)
def sync_factory_drive_images(
    factory_id: int,
    limit: int = Query(50, ge=1, le=500, description="أقصى عدد صور يُنسخ في هذا الاستدعاء"),
    db: Session = Depends(get_db),
):
    """ينسخ الآن صور مجلد Google Drive المشارَك من بريد المصنع (المسجَّل كجهة اتصال email) إلى مكتبته.
    يرجع ما نُسخ وما تبقى (remaining > 0 = أعد الاستدعاء). نفس ما تفعله الخدمة الدورية."""
    return drive_sync.sync_factory(db, factory_id, limit=limit)
