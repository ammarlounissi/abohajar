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


@router.put("/{factory_id}/categories", response_model=schemas.FactoryResponse)
def set_factory_categories(factory_id: int, body: schemas.FactoryCategoriesUpdate, db: Session = Depends(get_db)):
    """يحدد أصناف المصنع (الحروف في بداية الـ SKU)، وهي التي يراها في بوت «إضافة منتج»."""
    factory = db.query(models.Factory).filter(models.Factory.id == factory_id).first()
    if not factory:
        raise HTTPException(status_code=404, detail="المصنع غير موجود")
    factory.categories = body.categories
    db.commit()
    db.refresh(factory)
    return factory
