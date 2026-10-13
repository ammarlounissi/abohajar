from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.database import get_db
from app.core.security import require_admin

router = APIRouter(prefix="/api/factories", tags=["factories"], dependencies=[Depends(require_admin)])


@router.post("/", response_model=schemas.FactoryResponse, status_code=status.HTTP_201_CREATED)
def create_factory(factory: schemas.FactoryCreate, db: Session = Depends(get_db)):
    phone = factory.phone_number.lstrip("+")
    exists = db.query(models.Factory).filter(
        models.Factory.phone_number.in_([phone, f"+{phone}"])
    ).first()
    if exists:
        raise HTTPException(status_code=400, detail="رقم الهاتف مسجل بالفعل لمصنع آخر")

    new_factory = models.Factory(**factory.model_dump())
    db.add(new_factory)
    db.commit()
    db.refresh(new_factory)
    return new_factory


@router.get("/", response_model=List[schemas.FactoryResponse])
def get_factories(db: Session = Depends(get_db)):
    return db.query(models.Factory).all()


@router.get("/{factory_id}", response_model=schemas.FactoryResponse)
def get_factory(factory_id: int, db: Session = Depends(get_db)):
    factory = db.query(models.Factory).filter(models.Factory.id == factory_id).first()
    if not factory:
        raise HTTPException(status_code=404, detail="المصنع غير موجود")
    return factory


@router.patch("/{factory_id}", response_model=schemas.FactoryResponse)
def update_factory(factory_id: int, update: schemas.FactoryUpdate, db: Session = Depends(get_db)):
    factory = db.query(models.Factory).filter(models.Factory.id == factory_id).first()
    if not factory:
        raise HTTPException(status_code=404, detail="المصنع غير موجود")
    values = update.model_dump(exclude_unset=True)
    if "phone_number" in values:
        phone = values["phone_number"].lstrip("+")
        duplicate = db.query(models.Factory).filter(
            models.Factory.id != factory_id,
            models.Factory.phone_number.in_([phone, f"+{phone}"]),
        ).first()
        if duplicate:
            raise HTTPException(status_code=400, detail="رقم الهاتف مسجل بالفعل لمصنع آخر")
    for key, value in values.items():
        setattr(factory, key, value)
    db.commit()
    db.refresh(factory)
    return factory


@router.delete("/{factory_id}")
def delete_factory(factory_id: int, db: Session = Depends(get_db)):
    factory = db.query(models.Factory).filter(models.Factory.id == factory_id).first()
    if not factory:
        raise HTTPException(status_code=404, detail="المصنع غير موجود")
    if db.query(models.Product).filter(models.Product.factory_id == factory_id).first():
        raise HTTPException(status_code=409, detail="لا يمكن حذف مصنع لديه منتجات")
    if db.query(models.Post).filter(models.Post.factory_id == factory_id).first():
        raise HTTPException(status_code=409, detail="لا يمكن حذف مصنع لديه منشورات")
    db.delete(factory)
    db.commit()
    return {"status": "success", "message": "تم حذف المصنع بنجاح"}
