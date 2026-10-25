import enum
from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.time import utc_now


class Channel(str, enum.Enum):
    """قنوات التواصل مع المصانع. تُخزَّن في قاعدة البيانات نصاً عادياً (لا Enum في العمود)
    كي لا تحتاج قناة جديدة إلى ترحيل مخطط."""
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    EMAIL = "email"


class ChannelPlatform(str, enum.Enum):
    """منصة قناة النشر. القيم تطابق أسماء محوّلات النشر (services/social). تُخزَّن نصاً عادياً
    في social_channels.platform كي لا تحتاج منصة جديدة إلى ترحيل مخطط."""
    FACEBOOK = "facebook"      # صفحة فيسبوك
    INSTAGRAM = "instagram"    # حساب إنستغرام احترافي
    TELEGRAM = "telegram"      # قناة تيليجرام


class SyncStatus(str, enum.Enum):
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"


class Availability(str, enum.Enum):
    IN_STOCK = "in stock"
    OUT_OF_STOCK = "out of stock"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class PostStatus(str, enum.Enum):
    """دورة حياة المنشور: draft -> approved -> publishing -> published | failed.
    scheduled وarchived قديمتان وتبقيان كما هما (الناشر لا يلمسهما)."""
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    ARCHIVED = "archived"


class DeliveryStatus(str, enum.Enum):
    """حالة النشر على منصة واحدة. تُخزَّن نصاً عادياً في post_deliveries.status."""
    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class ReportKind(str, enum.Enum):
    """تصنيف أخطاء النشر. يُخزَّن نصاً عادياً في publish_reports.kind."""
    TOKEN = "token"
    MEDIA = "media"
    LIMIT = "limit"
    NETWORK = "network"
    UNKNOWN = "unknown"


class PostApproval(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Factory(Base):
    """أصحاب المصانع / الموردون"""
    __tablename__ = "factories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    activity = Column(Text, nullable=False, default="", server_default="")
    # قديم: رقم واتساب الأساسي. مصدر الحقيقة لتعريف المصنع في القنوات هو factory_contacts.
    phone_number = Column(String(20), unique=True, index=True, nullable=False)
    address = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)

    products = relationship("Product", back_populates="factory")
    posts = relationship("Post", back_populates="factory")
    social_channels = relationship("SocialChannel", back_populates="factory")
    media_items = relationship("Media", back_populates="factory", cascade="all, delete-orphan")
    contacts = relationship("FactoryContact", back_populates="factory", cascade="all, delete-orphan")
    chat_messages = relationship(
        "FactoryChatMessage", back_populates="factory", cascade="all, delete-orphan",
        order_by="FactoryChatMessage.id",
    )


class FactoryChatMessage(Base):
    """سؤال وردّ من محادثة Ollama، محفوظان كسجل مرتبط بمصنع."""
    __tablename__ = "factory_chat_messages"

    id = Column(Integer, primary_key=True)
    factory_id = Column(Integer, ForeignKey("factories.id"), nullable=False, index=True)
    prompt = Column(Text, nullable=False)
    response = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=utc_now)

    factory = relationship("Factory", back_populates="chat_messages")


class FactoryContact(Base):
    """جهة اتصال للمصنع في قناة ما (واتساب/تيليجرام/بريد). المصنع الواحد قد يملك عدة قنوات،
    وإضافة قناة جديدة لا تغيّر المخطط. identifier مُطبَّع حسب القناة (انظر services/factories.py)."""
    __tablename__ = "factory_contacts"
    __table_args__ = (UniqueConstraint("channel", "identifier", name="uq_factory_contacts_channel_identifier"),)

    id = Column(Integer, primary_key=True)
    factory_id = Column(Integer, ForeignKey("factories.id"), nullable=False, index=True)
    channel = Column(String(20), nullable=False)
    identifier = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=utc_now)

    factory = relationship("Factory", back_populates="contacts")


class Media(Base):
    """رابط صورة أو فيديو في مكتبة مصنع."""
    __tablename__ = "media"

    id = Column(Integer, primary_key=True)
    media_type = Column(String(10), nullable=False)
    url = Column(String(2000), nullable=False)
    description = Column(Text, nullable=True)
    factory_id = Column(Integer, ForeignKey("factories.id"), nullable=False, index=True)

    factory = relationship("Factory", back_populates="media_items")


class SocialChannel(Base):
    """قناة نشر (صفحة فيسبوك / حساب إنستغرام احترافي / قناة تيليجرام). تغني عن حفظ المعرّفات في الإعدادات:
    القناة تتبع مصنعاً واحداً، ورمز ميتا نفسه يخدم عدة صفحات للمصنع.
    remote_id هو المعرّف الأصلي عند المنصة، وis_active=False يوقف النشر إليها دون حذفها.
    (اسم الصنف ليس Channel لأن Channel قائم أصلاً لقنوات محادثة المصانع.)"""
    __tablename__ = "social_channels"
    __table_args__ = (UniqueConstraint("platform", "remote_id", name="uq_social_channels_platform_remote_id"),)

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String(30), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    remote_id = Column(String(100), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    factory_id = Column(Integer, ForeignKey("factories.id"), nullable=False, index=True)

    factory = relationship("Factory", back_populates="social_channels")

    @property
    def label(self) -> str:
        """وصف قصير للسجلات والطرفية: facebook:123456."""
        return f"{self.platform}:{self.remote_id}"


class Product(Base):
    """المنتجات، متوافقة مع Meta Commerce Graph API"""
    __tablename__ = "products"
    __table_args__ = (Index("ix_products_category_code", "category_code"),)

    id = Column(Integer, primary_key=True, index=True)

    sku = Column(String(50), unique=True, index=True, nullable=False)  # retailer_id
    category_code = Column(String(20), nullable=False)
    tags = Column(JSON, nullable=False, default=list)
    title = Column(String(255), nullable=False)
    commercial_name = Column(String(255), nullable=True)
    sizes = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=False)
    currency = Column(String(10), default="DZD", nullable=False)
    availability = Column(Enum(Availability), default=Availability.IN_STOCK)
    condition = Column(String(20), default="new")
    brand = Column(String(100), default="Hojrat Bladi")

    primary_media_url = Column(String(500), nullable=False)
    additional_media_urls = Column(JSON, default=list)
    video_url = Column(String(500), nullable=True)   # ريلز {IMAGE_BASE_URL}/{SKU}/1.mp4 (services/video_maker.py)

    meta_product_id = Column(String(100), unique=True, nullable=True)
    sync_status = Column(Enum(SyncStatus), default=SyncStatus.PENDING)
    sync_error_message = Column(Text, nullable=True)

    factory_id = Column(Integer, ForeignKey("factories.id"), nullable=False)
    factory = relationship("Factory", back_populates="products")

    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class Post(Base):
    """منشور مخصص للنشر على مواقع التواصل الاجتماعي.
    description هو نص المنشور (caption) الذي يُنشر كما هو. قنواته هي post_deliveries."""
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    factory_id = Column(Integer, ForeignKey("factories.id"), nullable=False, index=True)
    media_type = Column(Enum("text", "image", "gallery", "reel", name="postmediatype"), nullable=False)
    media_urls = Column(JSON, nullable=False, default=list)
    description = Column(Text, nullable=True)
    status = Column(Enum(PostStatus), nullable=False, default=PostStatus.DRAFT)
    approval = Column(Enum(PostApproval), nullable=False, default=PostApproval.PENDING)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    published_at = Column(DateTime, nullable=True)

    # --- النشر التلقائي (المرحلة الأولى) ---
    # وجهات النشر هي صفوف post_deliveries (قناة لكل صف، بمعرّفها الداخلي في social_channels)
    scheduled_at = Column(DateTime, nullable=True)        # لا يُنشر قبله (UTC)
    approved_at = Column(DateTime, nullable=True)
    claimed_at = Column(DateTime, nullable=True)          # وقت الحجز، لكشف العالقين
    error = Column(Text, nullable=True)                   # آخر خطأ مختصر
    sku = Column(String(50), nullable=True, index=True)   # إن وُجد يبقى كما هو، الناشر لا يلمسه

    factory = relationship("Factory", back_populates="posts")
    deliveries = relationship(
        "PostDelivery", back_populates="post", cascade="all, delete-orphan", order_by="PostDelivery.id",
    )


class PostDelivery(Base):
    """وجهة نشر واحدة لمنشور: قناة من social_channels (المنصة ومعرّفها الأصلي يُقرآن منها ولا يُكرَّران هنا).
    الصف يُنشأ عند تحديد قنوات المنشور بحالة pending، ثم تتبعه نتيجة النشر. القيد الفريد يمنع النشر المزدوج،
    وعند إعادة المحاولة تُنشر القنوات غير الناجحة وحدها. حذف قناة لها تسليمات مرفوض (عطّلها بدل حذفها)."""
    __tablename__ = "post_deliveries"
    __table_args__ = (
        UniqueConstraint("post_id", "channel_id", name="uq_post_deliveries_post_channel"),
    )

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    channel_id = Column(Integer, ForeignKey("social_channels.id", ondelete="RESTRICT"), nullable=False, index=True)
    # نص هذه القناة كما سيُنشر/نُشر (قالب كل منصة)؛ NULL = يُنشر posts.description
    caption = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default=DeliveryStatus.PENDING.value)
    remote_id = Column(String(100), nullable=True)
    error = Column(Text, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    published_at = Column(DateTime, nullable=True)

    post = relationship("Post", back_populates="deliveries")
    channel = relationship("SocialChannel")

    @property
    def platform(self) -> str:
        return self.channel.platform

    @property
    def channel_name(self) -> str:
        return self.channel.name

    @property
    def channel_remote_id(self) -> str:
        return self.channel.remote_id


class PublishReport(Base):
    """تقرير خطأ نشر. sent_at يُملأ لاحقاً عند إرساله إلى قناة (NULL الآن)."""
    __tablename__ = "publish_reports"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, default=utc_now)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=True, index=True)
    platform = Column(String(30), nullable=True)
    kind = Column(String(20), nullable=False, default=ReportKind.UNKNOWN.value)
    message = Column(Text, nullable=False)
    sent_at = Column(DateTime, nullable=True)


class Order(Base):
    """الطلبات (غير مستخدمة حالياً)"""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    customer_phone = Column(String(20), nullable=False)
    customer_name = Column(String(100), nullable=True)
    quantity = Column(Float, default=1.0)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    product = relationship("Product")


class ProcessedMessage(Base):
    """رسائل القنوات التي عولجت، لمنع المعالجة المكررة عند إعادة إرسال الـ webhook.
    المفتاح بصيغة "{channel}:{message_id}"."""
    __tablename__ = "processed_messages"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(128), unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=utc_now)


class PendingMediaUpdate(Base):
    """طلب إضافة بصورة لمنتج (أو مجلد) موجود مسبقاً، ينتظر جواب المصنع بنعم أو لا.
    لا نحمّل الصورة قبل الموافقة: نحفظ مرجعها في القناة فقط (media_ref: معرّف ميتا أو file_id في تيليجرام)،
    ويرتبط الطلب بالقناة التي جاء منها فلا يُجاب من قناة أخرى."""
    __tablename__ = "pending_media_updates"

    id = Column(Integer, primary_key=True)
    factory_id = Column(Integer, ForeignKey("factories.id"), nullable=False, index=True)
    sku = Column(String(50), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    price = Column(Float, nullable=False)
    channel = Column(String(20), nullable=False, default=Channel.WHATSAPP.value)
    media_ref = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
