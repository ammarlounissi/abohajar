"""قنوات النشر الاجتماعي (صفحات فيسبوك، حسابات إنستغرام، قنوات تيليجرام). لا تعرف شيئاً عن HTTP.

اتفاقية الحفظ: دوال create_/update_/delete_ تنفّذ commit بنفسها."""
import re
from typing import List, Optional

from sqlalchemy.orm import Session

from app import models
from app.models import ChannelPlatform
from app.services.errors import DuplicateError, InvalidInputError, NotFoundError

_NUMERIC_ID = re.compile(r"[0-9]{5,30}")
_TELEGRAM_ID = re.compile(r"-?[0-9]{5,20}|@[A-Za-z][A-Za-z0-9_]{4,31}")


def normalize_remote_id(platform: str, remote_id: str) -> str:
    """المعرّف الأصلي بصيغته المعتمدة، وبها يُخزَّن. ترفع InvalidInputError إن لم يصلح للمنصة.
    فيسبوك وإنستغرام: رقم (ID الصفحة أو حساب إنستغرام الاحترافي، وليس اسم المستخدم).
    تيليجرام: معرّف القناة الرقمي (-100...) أو @اسم_القناة العامة."""
    raw = (remote_id or "").strip()
    if platform in (ChannelPlatform.FACEBOOK.value, ChannelPlatform.INSTAGRAM.value):
        if not _NUMERIC_ID.fullmatch(raw):
            raise InvalidInputError("معرّف الصفحة/الحساب في ميتا رقم فقط (وليس اسم المستخدم)")
        return raw
    if platform == ChannelPlatform.TELEGRAM.value:
        if not _TELEGRAM_ID.fullmatch(raw):
            raise InvalidInputError("معرّف قناة تيليجرام: رقم مثل -1001234567890 أو @اسم_القناة")
        return raw
    raise InvalidInputError(f"منصة غير معروفة: {platform}")


def _platform_value(platform) -> str:
    return platform.value if isinstance(platform, ChannelPlatform) else str(platform)


def _exists(db: Session, platform: str, remote_id: str, exclude_id: Optional[int] = None) -> bool:
    query = db.query(models.SocialChannel).filter(
        models.SocialChannel.platform == platform,
        models.SocialChannel.remote_id == remote_id,
    )
    if exclude_id is not None:
        query = query.filter(models.SocialChannel.id != exclude_id)
    return query.first() is not None


def list_channels(
    db: Session, platform: Optional[str] = None, is_active: Optional[bool] = None,
) -> List[models.SocialChannel]:
    query = db.query(models.SocialChannel)
    if platform is not None:
        query = query.filter(models.SocialChannel.platform == _platform_value(platform))
    if is_active is not None:
        query = query.filter(models.SocialChannel.is_active.is_(is_active))
    return query.order_by(models.SocialChannel.id).all()


def list_active(db: Session, platform: str) -> List[models.SocialChannel]:
    """القنوات المفعّلة لمنصة واحدة: ما يقرأه الناشر بدل المعرّفات في الإعدادات."""
    return list_channels(db, platform=platform, is_active=True)


def get_channel(db: Session, channel_id: int) -> models.SocialChannel:
    channel = db.query(models.SocialChannel).filter(models.SocialChannel.id == channel_id).first()
    if not channel:
        raise NotFoundError("القناة غير موجودة")
    return channel


def create_channel(
    db: Session, *, platform, name: str, remote_id: str, is_active: bool = True,
) -> models.SocialChannel:
    platform = _platform_value(platform)
    remote_id = normalize_remote_id(platform, remote_id)
    if _exists(db, platform, remote_id):
        raise DuplicateError("هذه القناة مسجلة بالفعل")
    channel = models.SocialChannel(platform=platform, name=name, remote_id=remote_id, is_active=is_active)
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


def update_channel(db: Session, channel_id: int, values: dict) -> models.SocialChannel:
    """values: الحقول المراد تغييرها فقط (model_dump(exclude_unset=True)). المنصة لا تتغير."""
    channel = get_channel(db, channel_id)
    if "remote_id" in values:
        values = {**values, "remote_id": normalize_remote_id(channel.platform, values["remote_id"])}
        if _exists(db, channel.platform, values["remote_id"], exclude_id=channel.id):
            raise DuplicateError("هذه القناة مسجلة بالفعل")
    for key, value in values.items():
        setattr(channel, key, value)
    db.commit()
    db.refresh(channel)
    return channel


def delete_channel(db: Session, channel_id: int) -> None:
    channel = get_channel(db, channel_id)
    db.delete(channel)
    db.commit()
