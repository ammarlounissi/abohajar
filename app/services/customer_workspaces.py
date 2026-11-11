"""مساحة عمل الزبون: مكتبة وسائط الصفحة التي ربطها بخدمة. لا تعرف شيئاً عن HTTP.

القاعدة: كل صفحة مربوطة = مصنع خاص بها (models.Factory, kind=customer). الوسائط والفيديوهات والمنشورات
تتبع ذلك المصنع، لا المستخدم ولا صف القناة، فيُعاد استعمال مسار المصانع كله كما هو
(media_library، factory_videos، الناشر).

المصنع يُستخرج دائماً من (المستخدم، الصفحة المربوطة بالخدمة). لا يُقبل رقم مصنع من الزبون أبداً.

اتفاقية الحفظ: ensure_workspace لا تنفّذ commit (تُستدعى ضمن ربط الصفحة)؛ بقية الدوال تحفظ بنفسها."""
import io
from pathlib import Path
from typing import BinaryIO, List, Optional

from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

from app import models
from app.models import FactoryKind
from app.services import facebook_services, media_library
from app.services.errors import ConflictError, InvalidInputError

# حدود رفع الزبون. الصورة الواحدة بحد ما يقبله مولّد الفيديو (factory_videos.MAX_LIBRARY_IMAGE_*).
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
MAX_IMAGES = 200
# امتداد الملف -> الصيغة التي يجب أن يكون عليها محتواه فعلاً (كما يتعرّف عليها Pillow).
IMAGE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}


def _page_id(channel: models.SocialChannel) -> str:
    return channel.page_id or channel.remote_id


def _factory_name(channel: models.SocialChannel) -> str:
    name = (channel.page_name or channel.name or "").strip()[:100]
    return name if len(name) >= 2 else f"صفحة {_page_id(channel)}"[:100]


def ensure_workspace(db: Session, *, user_id: int, channel: models.SocialChannel) -> models.CustomerWorkspace:
    """مساحة هذا المستخدم لهذه الصفحة، وتُنشأ مع مصنعها عند أول مرة. تجعل القناة تابعة لمصنع المساحة
    كي ينشر الناشر عليها. لا commit.

    - صفحة أسندتها الإدارة إلى مصنع مورّد: تُعتمد مساحةً كما هي (الصفحة صفحته).
    - صفحة بقي صفها مشيراً إلى مساحة زبون سابق (صف مفكوك أُعيد ربطه): لا تُورَّث، يُنشأ مصنع جديد."""
    page_id = _page_id(channel)
    workspace = (
        db.query(models.CustomerWorkspace)
        .filter(models.CustomerWorkspace.user_id == user_id, models.CustomerWorkspace.page_id == page_id)
        .first()
    )
    if workspace is None:
        factory = channel.factory
        if factory is None or factory.kind != FactoryKind.SUPPLIER.value:
            factory = models.Factory(name=_factory_name(channel), kind=FactoryKind.CUSTOMER.value)
            db.add(factory)
        workspace = models.CustomerWorkspace(user_id=user_id, page_id=page_id, factory=factory)
        db.add(workspace)
    channel.factory = workspace.factory
    db.flush()
    return workspace


def current_workspace(db: Session, user_id: int, service_key: str) -> models.CustomerWorkspace:
    """مساحة الصفحة المربوطة بهذه الخدمة للمستخدم. NotFoundError لخدمة مجهولة، ConflictError إن لم يربط صفحة."""
    service = facebook_services.get_service(service_key)
    channel = facebook_services.connected_channel(db, user_id, service.key)
    if channel is None:
        raise ConflictError("اربط صفحتك بالخدمة أولاً")
    workspace = ensure_workspace(db, user_id=user_id, channel=channel)
    db.commit()
    return workspace


def _count(db: Session, factory_id: int, media_type: str) -> int:
    return (
        db.query(models.Media)
        .filter(models.Media.factory_id == factory_id, models.Media.media_type == media_type)
        .count()
    )


def describe(db: Session, user_id: int, service_key: str) -> dict:
    """ملخص المساحة كما تعرضه لوحة الزبون: الصفحة، عدد الوسائط، وحدود الرفع."""
    workspace = current_workspace(db, user_id, service_key)
    channel = facebook_services.connected_channel(db, user_id, service_key)
    return {
        "service": service_key,
        "page_id": workspace.page_id,
        "page_name": channel.page_name or channel.name,
        "images_count": _count(db, workspace.factory_id, "image"),
        "videos_count": _count(db, workspace.factory_id, "video"),
        "max_images": MAX_IMAGES,
        "max_image_bytes": MAX_IMAGE_BYTES,
        "image_extensions": sorted(IMAGE_FORMATS),
    }


def list_media(
    db: Session, user_id: int, service_key: str, *, media_type: Optional[str] = None,
    skip: int = 0, limit: int = 100,
) -> List[models.Media]:
    workspace = current_workspace(db, user_id, service_key)
    return media_library.list_factory_media(
        db, workspace.factory_id, media_type=media_type, skip=skip, limit=limit,
    )


def _read_image(file: BinaryIO, extension: str) -> bytes:
    """يقرأ الملف كله (بسقف الحجم) ويتحقق أن محتواه صورة فعلاً بالصيغة التي يدّعيها امتداده."""
    expected = IMAGE_FORMATS.get(extension)
    if expected is None:
        raise InvalidInputError("الصيغ المقبولة: JPG وPNG وWebP")
    data = file.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise InvalidInputError("لا يمكن رفع ملف فارغ")
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidInputError("حجم الصورة يتجاوز الحد الأقصى (20 ميغابايت)")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format
            pixels = image.width * image.height
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise InvalidInputError("الملف ليس صورة صالحة") from exc
    if image_format != expected:
        raise InvalidInputError("محتوى الملف لا يطابق امتداده")
    if pixels > MAX_IMAGE_PIXELS:
        raise InvalidInputError("أبعاد الصورة أكبر من الحد المسموح")
    return data


def upload_image(
    db: Session, user_id: int, service_key: str, *, file: BinaryIO, filename: Optional[str],
    content_type: Optional[str], description: Optional[str],
) -> models.Media:
    """يرفع صورة إلى مكتبة صفحة الزبون. أشد من رفع الإدارة: صور فقط، يُفحص محتواها، وبسقف للعدد."""
    workspace = current_workspace(db, user_id, service_key)
    extension = Path(filename or "").suffix.lower()
    data = _read_image(file, extension)
    if _count(db, workspace.factory_id, "image") >= MAX_IMAGES:
        raise InvalidInputError(f"بلغت الحد الأقصى لعدد الصور ({MAX_IMAGES}). احذف صوراً لترفع غيرها.")
    return media_library.upload_factory_media(
        db, workspace.factory_id, file=io.BytesIO(data), filename=f"image{extension}",
        content_type=content_type, description=description,
    )


def delete_media(db: Session, user_id: int, service_key: str, media_id: int) -> None:
    """يحذف وسيطاً من مكتبة صفحة الزبون فقط (NotFoundError لما ليس فيها)."""
    workspace = current_workspace(db, user_id, service_key)
    media_library.delete_factory_media(db, workspace.factory_id, media_id)
