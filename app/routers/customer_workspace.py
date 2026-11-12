"""مساحة الزبون لخدمة: مكتبة وسائط صفحته المربوطة وتوليد الفيديو منها. تتطلب جلسة مستخدم (لا مفتاح إدارة).

المصنع يُستخرج من الجلسة والصفحة المربوطة بالخدمة، ولا يظهر رقمه في أي مسار هنا.
401 بلا جلسة · 404 خدمة مجهولة أو عنصر ليس لصفحته · 409 لم يربط صفحة بالخدمة · 422 طلب أو ملف مرفوض."""
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app import schemas
from app.core.database import get_db
from app.routers.auth import get_current_user
from app.services import (
    customer_captions, customer_overview, customer_workspaces, post_drafts, post_publisher, push_notifications,
)

router = APIRouter(prefix="/api/services/{service_key}", tags=["customer-workspace"])


@router.get("/workspace", response_model=schemas.CustomerWorkspaceResponse)
def get_workspace(service_key: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return customer_workspaces.describe(db, user.id, service_key)


@router.get("/overview", response_model=schemas.CustomerOverview)
def get_overview(service_key: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """رئيسية اللوحة: مظهر الصفحة في فيسبوك (غلاف وبروفيل) وأرقام ما ينتظر الزبون وما أُنجز."""
    return customer_overview.overview(db, user.id, service_key)


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


# --- إعدادات الصفحة ونص المنشور بـ Gemini ---

@router.get("/profile", response_model=schemas.CustomerProfile)
def get_profile(service_key: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """إعدادات الصفحة؛ النشاط الفارغ يُجلب من صفحة فيسبوك (التصنيف والنبذة) ويُحفظ."""
    return customer_captions.get_profile(db, user.id, service_key)


@router.patch("/profile", response_model=schemas.CustomerProfile)
def update_profile(
    service_key: str, body: schemas.CustomerProfileUpdate,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """تعديل جزئي لإعدادات الصفحة: يُغيَّر ما أُرسل فقط."""
    return customer_captions.update_profile(db, user.id, service_key, body.model_dump(exclude_unset=True))


@router.post("/captions", response_model=schemas.CustomerCaptionJob, status_code=status.HTTP_202_ACCEPTED)
def request_caption(service_key: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """يضع كتابة الوصف في الطابور ويعيد المهمة فوراً؛ تُتابَع من /captions/{id}. 422 إن لم يكن للصفحة نشاط."""
    return customer_captions.request_caption(db, user.id, service_key)


@router.get("/captions/{job_id}", response_model=schemas.CustomerCaptionJob)
def get_caption_job(
    service_key: str, job_id: int, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    return customer_captions.get_caption_job(db, user.id, service_key, job_id)


# --- النشر الجماعي المجدول: مسودات تُراجع ويُوافق عليها، ثم تُرندر وتُنشر في مواعيدها ---

@router.get("/drafts", response_model=schemas.CustomerDrafts)
def list_drafts(service_key: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """المسودات والمنشورات المجدولة والمنشورة حديثاً، مع قيود الجدولة."""
    return post_drafts.list_drafts(db, user.id, service_key)


@router.post("/drafts", response_model=schemas.CustomerDrafts, status_code=status.HTTP_201_CREATED)
def generate_drafts(
    service_key: str, body: schemas.CustomerDraftsRequest,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """«توليد 30 مسودة»: مسودة لكل يوم ابتداءً من الغد في الساعة المختارة. لا يُرندر ولا يُنشر شيء قبل الموافقة.
    نصوصها تُكتب بـ Gemini واحداً بعد واحد (status=writing ثم ready). 422 بلا نشاط أو بلا صور أو فوق الحد."""
    return post_drafts.generate(db, user.id, service_key, count=body.count, at=body.time)


@router.post("/drafts/approve", response_model=schemas.CustomerDrafts)
def approve_drafts(
    service_key: str, body: schemas.CustomerDraftsApproval,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """الموافقة على المسودات التي راجعها الزبون (كلها ready)، وتسجيلها في سجل الموافقات."""
    return post_drafts.approve(db, user.id, service_key, body.ids)


@router.patch("/drafts/{draft_id}", response_model=schemas.CustomerDraft)
def update_draft(
    service_key: str, draft_id: int, body: schemas.CustomerDraftUpdate,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """تعديل النص (قبل الموافقة) أو الموعد (قبل النشر، بقيود الجدولة)."""
    return post_drafts.update_draft(db, user.id, service_key, draft_id, body.model_dump(exclude_unset=True))


@router.post("/drafts/{draft_id}/caption", response_model=schemas.CustomerDraft)
def rewrite_draft_caption(
    service_key: str, draft_id: int, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """نص جديد من Gemini لمسودة لم يُوافق عليها."""
    return post_drafts.rewrite_caption(db, user.id, service_key, draft_id)


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    service_key: str, draft_id: int, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """تُلغى المسودة ويُوقف منشورها (قبل النشر). تبقى في الملغاة 30 يوماً وتُسترجع بـ /restore."""
    post_drafts.delete_draft(db, user.id, service_key, draft_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/drafts/{draft_id}/restore", response_model=schemas.CustomerDraft)
def restore_draft(
    service_key: str, draft_id: int, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """يعيد مسودة ملغاة إلى «بانتظار المراجعة» (تُوافق عليها من جديد)، وينقل موعدها إن لم يعد صالحاً."""
    return post_drafts.restore_draft(db, user.id, service_key, draft_id)


# --- إشعارات المتصفح لأجهزة الصفحة ---

@router.get("/push", response_model=schemas.PushStatus)
def get_push_status(
    service_key: str, endpoint: Optional[str] = Query(None, max_length=1000),
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """هل الإشعارات متاحة، والمفتاح العام للاشتراك، وعدد أجهزة الصفحة، وهل هذا الجهاز (endpoint) مشترك."""
    return push_notifications.status(db, user.id, service_key, endpoint)


@router.post("/push/subscriptions", response_model=schemas.PushStatus)
def subscribe_push(
    service_key: str, body: schemas.PushSubscriptionIn,
    user_agent: Optional[str] = Header(None),
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """يحفظ اشتراك هذا الجهاز في إشعارات الصفحة (أو يحدّثه). 409 إن لم تُفعَّل الإشعارات على الخادم،
    و422 لعنوان ليس لخدمة دفع معروفة."""
    return push_notifications.subscribe(
        db, user.id, service_key, endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth,
        user_agent=user_agent,
    )


@router.post("/push/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe_push(
    service_key: str, body: schemas.PushEndpoint, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """يوقف إشعارات الصفحة على هذا الجهاز (لا خطأ إن لم يكن مشتركاً)."""
    push_notifications.unsubscribe(db, user.id, service_key, body.endpoint)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/push/test", response_model=schemas.PushTestResult)
def test_push(
    service_key: str, body: schemas.PushEndpoint, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """إشعار تجريبي لهذا الجهاز وحده. 404 إن لم يكن مشتركاً."""
    return {"sent": push_notifications.send_test(db, user.id, service_key, body.endpoint)}
