from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import schemas
from app.core.database import get_db
from app.core.security import require_admin
from app.services import factories as factory_service

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
