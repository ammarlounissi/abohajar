import re
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Availability, SyncStatus

SKU_PATTERN = r"^[A-Z0-9][A-Z0-9_-]{1,49}$"   # مثال: STONE-GLM-01


# --- المصانع ---
class FactoryBase(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    phone_number: str = Field(pattern=r"^\+?\d{8,15}$")   # بصيغة دولية، مثل +213550000000
    address: Optional[str] = Field(default=None, max_length=255)
    # أصناف المصنع: حروف لاتينية كبيرة، مثل ["B", "PK"]
    categories: List[str] = Field(default_factory=list)

    @field_validator("categories", mode="before")
    @classmethod
    def normalize_categories(cls, v):
        if v is None:
            return []
        cleaned = sorted({c.strip().upper() for c in v if isinstance(c, str) and c.strip()})
        if any(not re.fullmatch(r"[A-Z]+", c) for c in cleaned):
            raise ValueError("الصنف حروف لاتينية فقط، مثل B أو PK")
        return cleaned


class FactoryCreate(FactoryBase):
    pass


class FactoryCategoriesUpdate(BaseModel):
    categories: List[str] = Field(default_factory=list)

    _normalize = field_validator("categories", mode="before")(FactoryBase.normalize_categories.__func__)


class FactoryResponse(FactoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool


# --- المنتجات ---
class ProductBase(BaseModel):
    sku: str = Field(pattern=SKU_PATTERN)
    title: str = Field(min_length=2, max_length=255)
    description: Optional[str] = None
    price: float = Field(gt=0)
    currency: str = Field(default="DZD", min_length=3, max_length=3)
    availability: Availability = Availability.IN_STOCK
    condition: Literal["new", "refurbished", "used"] = "new"
    brand: str = "Hojrat Bladi"
    primary_media_url: str = Field(min_length=1, max_length=500)
    additional_media_urls: List[str] = Field(default_factory=list)
    video_url: Optional[str] = Field(default=None, max_length=500)

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_sku(cls, v):
        return v.strip().upper() if isinstance(v, str) else v


class ProductCreate(ProductBase):
    factory_id: int


class ProductUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=2, max_length=255)
    description: Optional[str] = None
    price: Optional[float] = Field(default=None, gt=0)
    availability: Optional[Availability] = None


class ProductResponse(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factory_id: int
    meta_product_id: Optional[str] = None
    sync_status: SyncStatus
    sync_error_message: Optional[str] = None


class ProductPublicResponse(BaseModel):
    """ما يراه الزوار فقط: بدون المصنع أو بيانات المزامنة مع ميتا."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku: str
    title: str
    description: Optional[str] = None
    price: float
    currency: str
    availability: Availability
    condition: str
    brand: str
    primary_media_url: str
    additional_media_urls: List[str] = Field(default_factory=list)
    video_url: Optional[str] = None
