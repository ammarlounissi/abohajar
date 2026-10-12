from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.database import get_db
from app.core.security import require_admin

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
