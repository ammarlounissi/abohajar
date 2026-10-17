from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.database import get_db
from app.core.security import require_admin
from app.services import post_publisher

router = APIRouter(
    prefix="/api/posts",
    tags=["posts"],
    dependencies=[Depends(require_admin)],
)


@router.post("/", response_model=schemas.PostResponse, status_code=status.HTTP_201_CREATED)
def create_post(post: schemas.PostCreate, db: Session = Depends(get_db)):
    if not db.query(models.Factory).filter(models.Factory.id == post.factory_id).first():
        raise HTTPException(status_code=404, detail="المصنع غير موجود")
    new_post = models.Post(**post.model_dump())
    db.add(new_post)
    db.commit()
    db.refresh(new_post)
    return new_post


@router.get("/", response_model=List[schemas.PostResponse])
def get_posts(db: Session = Depends(get_db)):
    return db.query(models.Post).order_by(models.Post.created_at.desc()).all()


# المسارات الثابتة قبل /{post_id} كي لا تُفسَّر "published" رقماً
@router.get("/published", response_model=List[schemas.PostResponse])
def get_published_posts(
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db),
):
    """المنشورات التي نُشرت، الأحدث نشراً أولاً."""
    return post_publisher.list_published(db, limit, offset)


@router.get("/unpublished", response_model=List[schemas.PostResponse])
def get_unpublished_posts(
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), db: Session = Depends(get_db),
):
    """غير المنشورة: draft وscheduled وapproved وpublishing وfailed (المؤرشفة مستثناة)."""
    return post_publisher.list_unpublished(db, limit, offset)


@router.post("/{post_id}/publish", response_model=schemas.PostResponse, status_code=status.HTTP_202_ACCEPTED)
def publish_post_now(post_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    """نشر يدوي فوري. يحجز المنشور ويرجع 202 بحالة publishing، والنشر يجري في الخلفية؛
    تابع النتيجة بـ GET /api/posts/{id} (حقل deliveries). يقبل draft وscheduled وapproved وfailed
    (الفاشل تُنشر منصاته الفاشلة وحدها)، ويتجاوز الموعد والسقف اليومي."""
    post = post_publisher.claim_post(db, post_id)
    background.add_task(post_publisher.publish_claimed_in_background, post_id)
    return post


@router.get("/{post_id}", response_model=schemas.PostResponse)
def get_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="المنشور غير موجود")
    return post


@router.patch("/{post_id}", response_model=schemas.PostResponse)
def update_post(post_id: int, update: schemas.PostUpdate, db: Session = Depends(get_db)):
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="المنشور غير موجود")
    values = update.model_dump(exclude_unset=True)
    if "factory_id" in values and not db.query(models.Factory).filter(
        models.Factory.id == values["factory_id"]
    ).first():
        raise HTTPException(status_code=404, detail="المصنع غير موجود")
    for key, value in values.items():
        setattr(post, key, value)
    db.commit()
    db.refresh(post)
    return post


@router.delete("/{post_id}")
def delete_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="المنشور غير موجود")
    db.delete(post)
    db.commit()
    return {"status": "success", "message": "تم حذف المنشور بنجاح"}
