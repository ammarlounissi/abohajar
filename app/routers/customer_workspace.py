"""مساحة الزبون لخدمة: مكتبة وسائط صفحته المربوطة. تتطلب جلسة مستخدم (لا مفتاح إدارة).

المصنع يُستخرج من الجلسة والصفحة المربوطة بالخدمة، ولا يظهر رقمه في أي مسار هنا.
401 بلا جلسة · 404 خدمة مجهولة · 409 لم يربط صفحة بالخدمة · 422 ملف مرفوض."""
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from app import schemas
from app.core.database import get_db
from app.routers.auth import get_current_user
from app.services import customer_workspaces

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
