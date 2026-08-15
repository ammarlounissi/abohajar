import enum
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Text, Enum, ForeignKey, DateTime, JSON, Boolean
from sqlalchemy.orm import relationship
from database import Base

# حالات المزامنة مع Meta Commerce API
class SyncStatus(str, enum.Enum):
    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"

# حالات التوفر حسب معايير Meta
class Availability(str, enum.Enum):
    IN_STOCK = "in stock"
    OUT_OF_STOCK = "out of stock"

# حالات الطلب
class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class Factory(Base):
    """جدول أصحاب المصانع / الموردين"""
    __tablename__ = "factories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    phone_number = Column(String(20), unique=True, index=True, nullable=False) # رقم الواتساب
    address = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # العلاقة مع المنتجات
    products = relationship("Product", back_populates="factory")


class Product(Base):
    """جدول المنتجات (الأحجار) المصمم ليتوافق مع Meta Commerce Graph API"""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    
    # حقول Meta Commerce الإلزامية والأساسية
    sku = Column(String(50), unique=True, index=True, nullable=False)  # معرّف الفريد للقطع (retailer_id)
    title = Column(String(255), nullable=False)                        # اسم المنتج (name/title)
    description = Column(Text, nullable=True)                          # الوصف التفصيلي
    price = Column(Float, nullable=False)                               # السعر
    currency = Column(String(10), default="DZD", nullable=False)        # العملة (الدينار الجزائري)
    availability = Column(Enum(Availability), default=Availability.IN_STOCK) # حالة التوفر
    condition = Column(String(20), default="new")                       # حالة المنتج (new/refurbished)
    brand = Column(String(100), default="Hojrat Bladi")                 # العلامة التجارية

    # الوسائط (صورة رئيسية + صور وفيديوهات إضافية)
    primary_media_url = Column(String(500), nullable=False)            # رابط الفيديو/الصورة الأساسي
    additional_media_urls = Column(JSON, default=[])                   # قائمة الروابط الإضافية

    # حقول المزامنة مع Meta Commerce API
    meta_product_id = Column(String(100), unique=True, nullable=True)  # ID المنتج في كتالوج Meta
    sync_status = Column(Enum(SyncStatus), default=SyncStatus.PENDING) # حالة المزامنة
    sync_error_message = Column(Text, nullable=True)                    # تفاصيل الخطأ إن وجد

    # ربط المنتج مع المصنع
    factory_id = Column(Integer, ForeignKey("factories.id"), nullable=False)
    factory = relationship("Factory", back_populates="products")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Order(Base):
    """جدول الطلبات الناتجة عن تفاعل الزبائن"""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    customer_phone = Column(String(20), nullable=False)
    customer_name = Column(String(100), nullable=True)
    quantity = Column(Float, default=1.0) # الكمية (مثلاً بالمتر المربع)
    status = Column(Enum(OrderStatus), default=OrderStatus.PENDING)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    product = relationship("Product")