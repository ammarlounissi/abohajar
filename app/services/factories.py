"""منطق المصانع وجهات اتصالها عبر القنوات. لا يعرف شيئاً عن HTTP ولا عن أي قناة بعينها.

اتفاقية الحفظ: دوال create_/update_/delete_/add_/remove_ تنفّذ commit بنفسها."""
import re
from typing import List, Optional

from sqlalchemy.orm import Session

from app import models
from app.models import Channel
from app.services.errors import ConflictError, DuplicateError, InvalidInputError, NotFoundError


def normalize_identifier(channel: str, identifier: str) -> str:
    """الصيغة الموحدة لمعرّف المرسل في القناة، وبها يُخزَّن ويُبحث.
    واتساب: أرقام فقط بالصيغة الدولية بلا +. تيليجرام: معرّف المستخدم الرقمي. بريد: أحرف صغيرة.
    ترفع InvalidInputError إن لم يصلح المعرّف للقناة."""
    raw = (identifier or "").strip()
    if channel == Channel.WHATSAPP.value:
        digits = re.sub(r"\D", "", raw)
        if not 8 <= len(digits) <= 15:
            raise InvalidInputError("رقم واتساب يجب أن يكون من 8 إلى 15 رقماً بالصيغة الدولية")
        return digits
    if channel == Channel.TELEGRAM.value:
        if not re.fullmatch(r"[0-9]{1,20}", raw):
            raise InvalidInputError("معرّف تيليجرام هو رقم المستخدم (user id) وليس اسم المستخدم")
        return raw
    if channel == Channel.EMAIL.value:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", raw):
            raise InvalidInputError("بريد إلكتروني غير صالح")
        return raw.lower()
    raise InvalidInputError(f"قناة غير معروفة: {channel}")


def find_by_contact(db: Session, channel: str, identifier: str) -> Optional[models.Factory]:
    """المصنع الفعّال المرتبط بهذا المعرّف في هذه القناة، أو None.
    هذه نقطة الدخول الوحيدة لمصادقة أي بوت: معرّف غير مسجَّل = تجاهل."""
    try:
        normalized = normalize_identifier(channel, identifier)
    except InvalidInputError:
        return None
    return (
        db.query(models.Factory)
        .join(models.FactoryContact, models.FactoryContact.factory_id == models.Factory.id)
        .filter(
            models.Factory.is_active.is_(True),
            models.FactoryContact.channel == channel,
            models.FactoryContact.identifier == normalized,
        )
        .first()
    )


# --- المصانع ---

def list_factories(db: Session) -> List[models.Factory]:
    return db.query(models.Factory).all()


def get_factory(db: Session, factory_id: int) -> models.Factory:
    factory = db.query(models.Factory).filter(models.Factory.id == factory_id).first()
    if not factory:
        raise NotFoundError("المصنع غير موجود")
    return factory


def _phone_taken(db: Session, phone: str, exclude_factory_id: Optional[int] = None) -> bool:
    digits = phone.lstrip("+")
    query = db.query(models.Factory).filter(models.Factory.phone_number.in_([digits, f"+{digits}"]))
    if exclude_factory_id is not None:
        query = query.filter(models.Factory.id != exclude_factory_id)
    if query.first():
        return True
    contact = db.query(models.FactoryContact).filter(
        models.FactoryContact.channel == Channel.WHATSAPP.value,
        models.FactoryContact.identifier == normalize_identifier(Channel.WHATSAPP.value, phone),
    )
    if exclude_factory_id is not None:
        contact = contact.filter(models.FactoryContact.factory_id != exclude_factory_id)
    return contact.first() is not None


def create_factory(db: Session, *, name: str, phone_number: str, address: Optional[str] = None) -> models.Factory:
    if _phone_taken(db, phone_number):
        raise DuplicateError("رقم الهاتف مسجل بالفعل لمصنع آخر")
    factory = models.Factory(name=name, phone_number=phone_number, address=address)
    factory.contacts.append(models.FactoryContact(
        channel=Channel.WHATSAPP.value,
        identifier=normalize_identifier(Channel.WHATSAPP.value, phone_number),
    ))
    db.add(factory)
    db.commit()
    db.refresh(factory)
    return factory


def update_factory(db: Session, factory_id: int, values: dict) -> models.Factory:
    """values: الحقول المراد تغييرها فقط (model_dump(exclude_unset=True))."""
    factory = get_factory(db, factory_id)
    if "phone_number" in values:
        if _phone_taken(db, values["phone_number"], exclude_factory_id=factory_id):
            raise DuplicateError("رقم الهاتف مسجل بالفعل لمصنع آخر")
        # الرقم القديم في العمود هو جهة اتصال واتساب الأساسية، فنُبقي الاثنين متطابقين
        digits = normalize_identifier(Channel.WHATSAPP.value, values["phone_number"])
        whatsapp = next((c for c in factory.contacts if c.channel == Channel.WHATSAPP.value), None)
        if whatsapp:
            whatsapp.identifier = digits
        else:
            factory.contacts.append(models.FactoryContact(channel=Channel.WHATSAPP.value, identifier=digits))
    for key, value in values.items():
        setattr(factory, key, value)
    db.commit()
    db.refresh(factory)
    return factory


def delete_factory(db: Session, factory_id: int) -> None:
    factory = get_factory(db, factory_id)
    if db.query(models.Product).filter(models.Product.factory_id == factory_id).first():
        raise ConflictError("لا يمكن حذف مصنع لديه منتجات")
    if db.query(models.Post).filter(models.Post.factory_id == factory_id).first():
        raise ConflictError("لا يمكن حذف مصنع لديه منشورات")
    db.delete(factory)
    db.commit()


# --- جهات الاتصال ---

def list_contacts(db: Session, factory_id: int) -> List[models.FactoryContact]:
    return get_factory(db, factory_id).contacts


def add_contact(db: Session, factory_id: int, channel: str, identifier: str) -> models.FactoryContact:
    factory = get_factory(db, factory_id)
    normalized = normalize_identifier(channel, identifier)
    taken = db.query(models.FactoryContact).filter(
        models.FactoryContact.channel == channel,
        models.FactoryContact.identifier == normalized,
    ).first()
    if taken:
        raise DuplicateError("هذا المعرّف مسجل بالفعل لمصنع في هذه القناة")
    contact = models.FactoryContact(factory_id=factory.id, channel=channel, identifier=normalized)
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def remove_contact(db: Session, factory_id: int, contact_id: int) -> None:
    contact = db.query(models.FactoryContact).filter(
        models.FactoryContact.id == contact_id,
        models.FactoryContact.factory_id == factory_id,
    ).first()
    if not contact:
        raise NotFoundError("جهة الاتصال غير موجودة")
    db.delete(contact)
    db.commit()
