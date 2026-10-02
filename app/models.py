import enum
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, Float, ForeignKey, Integer, JSON, String, Text,
)
from sqlalchemy.orm import relationship

from app.core.database import Base


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


class Factory(Base):
    """أصحاب المصانع / الموردون"""
    __tablename__ = "factories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    phone_number = Column(String(20), unique=True, index=True, nullable=False)
    address = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    products = relationship("Product", back_populates="factory")


class Product(Base):
    """المنتجات، متوافقة مع Meta Commerce Graph API"""
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)

    sku = Column(String(50), unique=True, index=True, nullable=False)  # retailer_id
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
    """رسائل واتساب التي عولجت، لمنع المعالجة المكررة عند إعادة إرسال ميتا للـ webhook"""
    __tablename__ = "processed_messages"

    id = Column(Integer, primary_key=True)
    message_id = Column(String(128), unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
