"""منع المعالجة المكررة لرسائل القنوات (إعادة إرسال الـ webhook)."""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models


def claim_message(db: Session, channel: str, message_id: str) -> bool:
    """تسجيل الرسالة بشكل ذري. True = جديدة فعالجها، False = مكررة فتجاهلها."""
    db.add(models.ProcessedMessage(message_id=f"{channel}:{message_id}"))
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
