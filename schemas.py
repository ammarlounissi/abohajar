from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from enum import Enum

# تعريف أنواع التوفر الحالات لـ Pydantic
class AvailabilityEnum(str, Enum):
    IN_STOCK = "in stock"
    OUT_OF_STOCK = "out of stock"

# --- مخططات المصانع (Factory Schemas) ---
class FactoryBase(BaseModel):
    name: str
    phone_number: str
    address: Optional[str] = None

class FactoryCreate(FactoryBase):
    pass

class FactoryResponse(FactoryBase):
    id: int
    is_active: bool

    class Config:
        from_attributes = True

# --- مخططات المنتجات (Product Schemas) ---
class ProductBase(BaseModel):
    sku: str
    title: str
    description: Optional[str] = None
    price: float
    currency: str = "DZD"
    availability: AvailabilityEnum = AvailabilityEnum.IN_STOCK
    condition: str = "new"
    brand: str = "Hojrat Bladi"
    primary_media_url: str
    additional_media_urls: Optional[List[str]] = []

class ProductCreate(ProductBase):
    factory_id: int

class ProductResponse(ProductBase):
    id: int
    meta_product_id: Optional[str] = None
    sync_status: str
    factory_id: int

    class Config:
        from_attributes = True