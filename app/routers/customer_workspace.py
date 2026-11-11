"""مساحة الزبون لخدمة: مكتبة وسائط صفحته المربوطة وتوليد الفيديو منها. تتطلب جلسة مستخدم (لا مفتاح إدارة).

المصنع يُستخرج من الجلسة والصفحة المربوطة بالخدمة، ولا يظهر رقمه في أي مسار هنا.
401 بلا جلسة · 404 خدمة مجهولة أو عنصر ليس لصفحته · 409 لم يربط صفحة بالخدمة · 422 طلب أو ملف مرفوض."""
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import schemas
from app.core.database import get_db
from app.routers.auth import get_current_user
from app.services import customer_workspaces, post_publisher

router = APIRouter(prefix="/api/services/{service_key}", tags=["customer-workspace"])


@router.get("/workspace", response_model=schemas.CustomerWorkspaceResponse)
def get_workspace(service_key: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return customer_workspaces.describe(db, user.id, service_key)


@router.get("/media", response_model=List[schemas.CustomerMediaResponse])
def list_media(
    service_key: str,
    media_type: Optional[str] = Query(None, pattern="^(image|video)$"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return customer_workspaces.list_media(
        db, user.id, service_key, media_type=media_type, skip=skip, limit=limit,
    )


@router.post("/media", response_model=schemas.CustomerMediaResponse, status_code=status.HTTP_201_CREATED)
def upload_image(
    service_key: str,
    file: UploadFile = File(...),
    description: Optional[str] = Form(None, max_length=2000),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return customer_workspaces.upload_image(
            db, user.id, service_key, file=file.file, filename=file.filename,
            content_type=file.content_type, description=description,
        )
    finally:
        file.file.close()


@router.delete("/media/{media_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_media(
    service_key: str, media_id: int, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    customer_workspaces.delete_media(db, user.id, service_key, media_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- توليد الفيديو من صور المكتبة ---

@router.get("/videos/options", response_model=schemas.CustomerVideoOptions)
def get_video_options(service_key: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return customer_workspaces.video_options(db, user.id, service_key)


@router.post("/videos", response_model=schemas.CustomerVideoJob, status_code=status.HTTP_202_ACCEPTED)
def request_video(
    service_key: str, body: schemas.CustomerVideoRequest,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """يضع توليد الفيديو في الطابور ويعيد المهمة فوراً؛ تُتابَع حالتها من /videos/jobs/{id}."""
    return customer_workspaces.request_video(
        db, user.id, service_key, template=body.template, audio=body.audio, image_ids=body.image_ids,
    )


@router.get("/videos/audio/{name}", response_class=FileResponse)
def get_audio(service_key: str, name: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """ملف صوت من قائمة /videos/options، لتسمعه المعاينة في اللوحة قبل التوليد."""
    path = customer_workspaces.audio_file(db, user.id, service_key, name)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


@router.get("/videos/jobs", response_model=List[schemas.CustomerVideoJob])
def list_video_jobs(
    service_key: str, limit: int = Query(10, ge=1, le=50),
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    return customer_workspaces.list_video_jobs(db, user.id, service_key, limit=limit)


@router.get("/videos/jobs/{job_id}", response_model=schemas.CustomerVideoJob)
def get_video_job(
    service_key: str, job_id: int, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    return customer_workspaces.get_video_job(db, user.id, service_key, job_id)


# --- نشر الفيديو على الصفحة المربوطة ---

@router.get("/videos/posts", response_model=List[schemas.CustomerVideoPost])
def list_video_posts(service_key: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """حالة نشر كل فيديو (آخر منشور له)."""
    return customer_workspaces.list_video_posts(db, user.id, service_key)


@router.post(
    "/videos/{media_id}/publish", response_model=schemas.CustomerVideoPost,
    status_code=status.HTTP_202_ACCEPTED,
)
def publish_video(
    service_key: str, media_id: int, body: schemas.CustomerPublishRequest, background: BackgroundTasks,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """نشر فوري على الصفحة المربوطة: يرجع 202 بحالة publishing والنشر يجري بعد الرد؛
    تُتابَع النتيجة من /videos/posts. الفاشل تُعاد محاولته بالطلب نفسه."""
    view = customer_workspaces.publish_video(db, user.id, service_key, media_id, caption=body.caption)
    background.add_task(post_publisher.publish_claimed_in_background, view["id"])
    return view
