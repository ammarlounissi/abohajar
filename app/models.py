import enum
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


class Channel(str, enum.Enum):
    """قنوات التواصل مع المصانع. تُخزَّن في قاعدة البيانات نصاً عادياً (لا Enum في العمود)
    كي لا تحتاج قناة جديدة إلى ترحيل مخطط."""
    WHATSAPP = "whatsapp"
    TELEGRAM = "telegram"
    EMAIL = "email"


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
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class PostApproval(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Factory(Base):
    """أصحاب المصانع / الموردون"""
    __tablename__ = "factories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    # قديم: رقم واتساب الأساسي. مصدر الحقيقة لتعريف المصنع في القنوات هو factory_contacts.
    phone_number = Column(String(20), unique=True, index=True, nullable=False)
    address = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    products = relationship("Product", back_populates="factory")
    posts = relationship("Post", back_populates="factory")
    contacts = relationship("FactoryContact", back_populates="factory", cascade="all, delete-orphan")


class FactoryContact(Base):
    """جهة اتصال للمصنع في قناة ما (واتساب/تيليجرام/بريد). المصنع الواحد قد يملك عدة قنوات،
    وإضافة قناة جديدة لا تغيّر المخطط. identifier مُطبَّع حسب القناة (انظر services/factories.py)."""
    __tablename__ = "factory_contacts"
    __table_args__ = (UniqueConstraint("channel", "identifier", name="uq_factory_contacts_channel_identifier"),)

    id = Column(Integer, primary_key=True)
    factory_id = Column(Integer, ForeignKey("factories.id"), nullable=False, index=True)
    channel = Column(String(20), nullable=False)
    identifier = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    factory = relationship("Factory", back_populates="contacts")


class Product(Base):
    """المنتجات، متوافقة مع Meta Commerce Graph API"""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)

    sku = Column(String(50), unique=True, index=True, nullable=False)  # retailer_id
    category_code = Column(String(20), nullable=False, index=True)
    tags = Column(JSON, nullable=False, default=list)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(Float, nullable=False)
    currency = Column(String(10), default="DZD", nullable=False)
    availability = Column(Enum(Availability), default=Availability.IN_STOCK)
    condition = Column(String(20), default="new")
    brand = Column(String(100), default="Hojrat Bladi")

    primary_media_url = Column(String(500), nullable=False)
    additional_media_urls = Column(JSON, default=list)

    meta_product_id = Column(String(100), unique=True, nullable=True)
    sync_status = Column(Enum(SyncStatus), default=SyncStatus.PENDING)
    sync_error_message = Column(Text, nullable=True)

    factory_id = Column(Integer, ForeignKey("factories.id"), nullable=False)
    factory = relationship("Factory", back_populates="products")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Post(Base):
    """منشور مخصص للنشر على مواقع التواصل الاجتماعي."""
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    factory_id = Column(Integer, ForeignKey("factories.id"), nullable=False, index=True)
    media_type = Column(Enum("image", "gallery", "reel", name="postmediatype"), nullable=False)
    media_urls = Column(JSON, nullable=False, default=list)
    description = Column(Text, nullable=True)
    status = Column(Enum(PostStatus), nullable=False, default=PostStatus.DRAFT)
    approval = Column(Enum(PostApproval), nullable=False, default=PostApproval.PENDING)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    published_at = Column(DateTime, nullable=True)

    factory = relationship("Factory", back_populates="posts")


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
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product")


class ProcessedMessage(Base):
    """رسائل القنوات التي عولجت، لمنع المعالجة المكررة عند إعادة إرسال الـ webhook.
    المفتاح بصيغة "{channel}:{message_id}"."""
    __tablename__ = "processed_messages"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(128), unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
