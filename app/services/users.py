"""مستخدمو لوحة التحكم: دخول بالبريد وكلمة السر أو بفيسبوك. لا تعرف شيئاً عن HTTP.

كلمات السر تُخزَّن بصمة scrypt (مكتبة بايثون القياسية، بلا اعتماد خارجي) بصيغة
scrypt$N$r$p$salt$hash. اتفاقية الحفظ: دوال create_/login_ تنفّذ commit بنفسها."""
import base64
import hashlib
import hmac
import re
import secrets
from typing import Optional

from sqlalchemy.orm import Session

from app import models
from app.core.time import utc_now
from app.services.errors import ConflictError, DuplicateError, InvalidInputError, NotFoundError

MIN_PASSWORD_LENGTH = 8
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_BAD_LOGIN = "البريد أو كلمة السر غير صحيحة"


def normalize_email(email: str) -> str:
    value = (email or "").strip().lower()
    if len(value) > 255 or not _EMAIL.fullmatch(value):
        raise InvalidInputError("البريد الإلكتروني غير صالح")
    return value


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def hash_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise InvalidInputError(f"كلمة السر يجب ألا تقل عن {MIN_PASSWORD_LENGTH} أحرف")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt),
            n=int(n), r=int(r), p=int(p), dklen=32,
        )
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


# بصمة وهمية: تُستعمل حين لا يوجد المستخدم كي يستغرق الرد الوقت نفسه فلا يُكشف وجود البريد
_DUMMY_HASH = hash_password("dummy-password-for-timing")


def create_user_with_password(
    db: Session, email: str, password: str, name: str = "",
    role: str = "user", factory_id: Optional[int] = None,
) -> models.User:
    email = normalize_email(email)
    if db.query(models.User).filter(models.User.email == email).first():
        raise DuplicateError("هذا البريد مسجَّل مسبقاً")
    user = models.User(
        email=email, password_hash=hash_password(password), name=(name or "").strip(),
        role=role, factory_id=factory_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login_with_password(db: Session, email: str, password: str) -> models.User:
    """يرجع المستخدم إن صحّ البريد وكلمة السر وكان الحساب مفعّلاً، وإلا InvalidInputError برسالة واحدة
    لكل الحالات (بريد غير موجود، كلمة خاطئة، حساب فيسبوك فقط، حساب معطّل)."""
    try:
        email = normalize_email(email)
    except InvalidInputError:
        raise InvalidInputError(_BAD_LOGIN)
    user = db.query(models.User).filter(models.User.email == email).first()
    stored = user.password_hash if user and user.password_hash else _DUMMY_HASH
    ok = verify_password(password or "", stored)
    if not (user and user.password_hash and user.is_active and ok):
        raise InvalidInputError(_BAD_LOGIN)
    user.last_login_at = utc_now()
    db.commit()
    return user


def login_with_facebook(
    db: Session, facebook_id: str, name: str = "", email: Optional[str] = None,
) -> models.User:
    """يدخل (وينشئ عند أول مرة) المستخدم صاحب facebook_id الذي أكّده فيسبوك للتو.
    لا يربط الحساب ببريد موجود عند مستخدم آخر تلقائياً (يمنع الاستيلاء على حساب بالبريد):
    في هذه الحالة يُنشأ حساب فيسبوك بلا بريد."""
    facebook_id = (facebook_id or "").strip()
    if not facebook_id:
        raise InvalidInputError("معرّف فيسبوك مطلوب")
    user = db.query(models.User).filter(models.User.facebook_id == facebook_id).first()
    if user is None:
        safe_email = None
        if email:
            try:
                candidate = normalize_email(email)
            except InvalidInputError:
                candidate = None
            if candidate and not db.query(models.User).filter(models.User.email == candidate).first():
                safe_email = candidate
        user = models.User(facebook_id=facebook_id, email=safe_email, name=(name or "").strip())
        db.add(user)
    elif not user.is_active:
        raise InvalidInputError("الحساب معطّل")
    elif name and not user.name:
        user.name = name.strip()
    user.last_login_at = utc_now()
    db.flush()
    _claim_legacy_channels(db, user)
    db.commit()
    db.refresh(user)
    return user


def get_active_user(db: Session, user_id: int) -> models.User:
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None or not user.is_active:
        raise NotFoundError("المستخدم غير موجود أو معطّل")
    return user


def attach_facebook(db: Session, user: models.User, facebook_id: str) -> models.User:
    """يربط حساب فيسبوك (أكّده فيسبوك للتو) بمستخدم دخل بالبريد. حساب فيسبوك واحد لكل مستخدم،
    ولا يُنقل حساب فيسبوك مرتبط بمستخدم آخر."""
    facebook_id = (facebook_id or "").strip()
    if not facebook_id:
        raise InvalidInputError("معرّف فيسبوك مطلوب")
    if user.facebook_id and user.facebook_id != facebook_id:
        raise ConflictError("حسابك مرتبط بحساب فيسبوك آخر")
    other = db.query(models.User).filter(models.User.facebook_id == facebook_id).first()
    if other is not None and other.id != user.id:
        raise DuplicateError("حساب فيسبوك هذا مرتبط بمستخدم آخر")
    user.facebook_id = facebook_id
    db.flush()
    _claim_legacy_channels(db, user)
    db.commit()
    return user


def _claim_legacy_channels(db: Session, user: models.User) -> None:
    """قنوات رُبطت قبل وجود المستخدمين (user_id فارغ) تُنسب لصاحب حساب فيسبوك المطابق لـ owner_fbid."""
    if not user.facebook_id:
        return
    (
        db.query(models.SocialChannel)
        .filter(models.SocialChannel.owner_fbid == user.facebook_id, models.SocialChannel.user_id.is_(None))
        .update({"user_id": user.id}, synchronize_session=False)
    )


def remove_facebook_identity(db: Session, facebook_id: str) -> None:
    """تنفيذ طلب حذف البيانات من فيسبوك: من له بريد وكلمة سر يبقى حسابه بلا فيسبوك، وغيره يُحذف.
    قنواته تُحذف قبل ذلك (انظر social_channels.delete_by_owner_fbid) وما تبقى منها يُفكّ ربطه."""
    user = db.query(models.User).filter(models.User.facebook_id == facebook_id).first()
    if user is None:
        pass
    elif user.email and user.password_hash:
        user.facebook_id = None
    else:
        db.query(models.SocialChannel).filter(models.SocialChannel.user_id == user.id).update(
            {"user_id": None}, synchronize_session=False
        )
        db.delete(user)
    db.commit()
