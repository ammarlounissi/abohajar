"""قنوات النشر الاجتماعي (صفحات فيسبوك، حسابات إنستغرام، قنوات تيليجرام). لا تعرف شيئاً عن HTTP.

اتفاقية الحفظ: دوال create_/update_/delete_ تنفّذ commit بنفسها."""
import re
from typing import List, Optional

from sqlalchemy.orm import Session

from app import models
from app.models import ChannelPlatform
from app.services.errors import ConflictError, DuplicateError, InvalidInputError, NotFoundError

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
    factory_id: Optional[int] = None, bot_enabled: Optional[bool] = None,
) -> List[models.SocialChannel]:
    query = db.query(models.SocialChannel)
    if platform is not None:
        query = query.filter(models.SocialChannel.platform == _platform_value(platform))
    if is_active is not None:
        query = query.filter(models.SocialChannel.is_active.is_(is_active))
    if factory_id is not None:
        query = query.filter(models.SocialChannel.factory_id == factory_id)
    if bot_enabled is not None:
        query = query.filter(models.SocialChannel.bot_enabled.is_(bot_enabled))
    return query.order_by(models.SocialChannel.id).all()


def list_active(db: Session, platform: str, factory_id: int) -> List[models.SocialChannel]:
    """القنوات المفعّلة لمنصة واحدة: ما يقرأه الناشر بدل المعرّفات في الإعدادات."""
    return list_channels(db, platform=platform, is_active=True, factory_id=factory_id)


def get_active_facebook_page(db: Session, page_id: str) -> Optional[models.SocialChannel]:
    """يرجع صفحة Facebook المؤهلة للبوت التي يخصها webhook، أو None.

    يعتمد على ``bot_enabled`` لا ``is_active``؛ يمكن لرمز وصول واحد أن يخدم عدة صفحات.
    """
    normalized = (page_id or "").strip()
    if not normalized:
        return None
    return (
        db.query(models.SocialChannel)
        .filter(
            models.SocialChannel.platform == ChannelPlatform.FACEBOOK.value,
            models.SocialChannel.remote_id == normalized,
            models.SocialChannel.bot_enabled.is_(True),
        )
        .first()
    )


def get_channel(db: Session, channel_id: int) -> models.SocialChannel:
    channel = db.query(models.SocialChannel).filter(models.SocialChannel.id == channel_id).first()
    if not channel:
        raise NotFoundError("القناة غير موجودة")
    return channel


def create_channel(
    db: Session, *, factory_id: Optional[int] = None, platform, name: str,
    description: str = "", remote_id: str,
    is_active: bool = True, bot_enabled: bool = True,
) -> models.SocialChannel:
    if factory_id is not None and not db.get(models.Factory, factory_id):
        raise NotFoundError("المصنع غير موجود")
    platform = _platform_value(platform)
    remote_id = normalize_remote_id(platform, remote_id)
    if _exists(db, platform, remote_id):
        raise DuplicateError("هذه القناة مسجلة بالفعل")
    channel = models.SocialChannel(
        factory_id=factory_id, platform=platform, name=name,
        description=description or "", remote_id=remote_id,
        is_active=is_active, bot_enabled=bot_enabled,
    )
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
    if db.query(models.PostDelivery).filter(models.PostDelivery.channel_id == channel_id).first():
        raise ConflictError("للقناة منشورات مرتبطة بها: عطّلها (is_active=false) بدل حذفها")
    db.delete(channel)
    db.commit()


def link_facebook_page(
    db: Session, *, user_id: int, page_id: str, page_name: str, page_token: str,
    owner_fbid: Optional[str], owner_name: Optional[str],
) -> Optional[models.SocialChannel]:
    """يربط صفحة (من Facebook Login) بالمستخدم. لا تنفّذ commit (المستدعي يحفظ دفعة واحدة).
    ترجع None دون تغيير إن كانت الصفحة مربوطة بمستخدم آخر: لا تُسرق صفحة بإعادة الربط."""
    channel = db.query(models.SocialChannel).filter(models.SocialChannel.page_id == page_id).first()
    label = page_name or page_id
    if channel is None:
        channel = models.SocialChannel(
            platform=ChannelPlatform.FACEBOOK.value, name=label,
            description=f"صفحة Facebook: {label}", remote_id=page_id, page_id=page_id,
            page_name=label, page_access_token=page_token, owner_fbid=owner_fbid,
            owner_name=owner_name, factory_id=None, user_id=user_id,
            is_active=True, bot_enabled=True,
        )
        db.add(channel)
        return channel
    if channel.user_id is not None and channel.user_id != user_id:
        return None
    channel.user_id = user_id
    channel.name = page_name or channel.name
    channel.description = channel.description or f"صفحة Facebook: {label}"
    channel.remote_id = page_id
    channel.page_name = page_name or channel.page_name
    channel.page_access_token = page_token
    channel.owner_fbid = owner_fbid or channel.owner_fbid
    channel.owner_name = owner_name or channel.owner_name
    channel.is_active = True
    channel.bot_enabled = True
    return channel


def list_user_facebook_pages(db: Session, user_id: int) -> List[models.SocialChannel]:
    """صفحات فيسبوك التي يملكها هذا المستخدم فقط."""
    return (
        db.query(models.SocialChannel)
        .filter(
            models.SocialChannel.platform == ChannelPlatform.FACEBOOK.value,
            models.SocialChannel.user_id == user_id,
        )
        .order_by(models.SocialChannel.id)
        .all()
    )


def delete_by_owner_fbid(db: Session, owner_fbid: str) -> int:
    """يحذف كل قنوات صاحب حساب فيسبوك (طلبا deauthorize/delete من فيسبوك). لا commit."""
    return (
        db.query(models.SocialChannel)
        .filter(models.SocialChannel.owner_fbid == owner_fbid)
        .delete(synchronize_session=False)
    )


def get_user_facebook_page(db: Session, user_id: int, page_id: str) -> models.SocialChannel:
    """صفحة فيسبوك يملكها هذا المستخدم. صفحة غيره كغير الموجودة (لا نكشف وجودها)."""
    channel = (
        db.query(models.SocialChannel)
        .filter(
            models.SocialChannel.platform == ChannelPlatform.FACEBOOK.value,
            models.SocialChannel.page_id == page_id,
            models.SocialChannel.user_id == user_id,
        )
        .first()
    )
    if channel is None:
        raise NotFoundError("الصفحة غير موجودة")
    return channel


def remove_user_facebook_page(db: Session, channel: models.SocialChannel) -> str:
    """يحذف صفحة المستخدم وبياناتها (متطلب فيسبوك). يرجع "deleted" أو "detached".
    إن كانت للصفحة منشورات منشورة/مجدولة (post_deliveries تمنع حذف الصف) يبقى الصف مفرَّغاً للسجل فقط:
    يُمحى التوكن وبيانات المالك ويُفكّ ربطه بالمستخدم ويُعطَّل. إعادة ربط الصفحة لاحقاً تعيد تفعيله."""
    has_deliveries = (
        db.query(models.PostDelivery).filter(models.PostDelivery.channel_id == channel.id).first() is not None
    )
    if not has_deliveries:
        db.delete(channel)
        db.commit()
        return "deleted"
    channel.page_access_token = None
    channel.owner_fbid = None
    channel.owner_name = None
    channel.user_id = None
    channel.is_active = False
    channel.bot_enabled = False
    db.commit()
    return "detached"
