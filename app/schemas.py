from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import Availability, Channel, PostApproval, PostStatus, SyncStatus

SKU_PATTERN = r"^[A-Z0-9][A-Z0-9_-]{1,49}$"   # مثال: STONE-GLM-01


# --- المصانع ---
class FactoryBase(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    phone_number: str = Field(pattern=r"^\+?\d{8,15}$")   # بصيغة دولية، مثل +213550000000
    address: Optional[str] = Field(default=None, max_length=255)


class FactoryCreate(FactoryBase):
    pass


class FactoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    phone_number: Optional[str] = Field(default=None, pattern=r"^\+?\d{8,15}$")
    address: Optional[str] = Field(default=None, max_length=255)
    is_active: Optional[bool] = None


class FactoryResponse(FactoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool


class FactoryContactCreate(BaseModel):
    channel: Channel
    identifier: str = Field(min_length=1, max_length=100)


class FactoryContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factory_id: int
    channel: str
    identifier: str


# --- المنتجات ---
class ProductBase(BaseModel):
    sku: str = Field(pattern=SKU_PATTERN)
    category_code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    tags: List[str] = Field(default_factory=list, max_length=30)
    title: str = Field(min_length=2, max_length=255)
    description: Optional[str] = None
    price: float = Field(gt=0)
    currency: str = Field(default="DZD", min_length=3, max_length=3)
    availability: Availability = Availability.IN_STOCK
    condition: Literal["new", "refurbished", "used"] = "new"
    brand: str = "Hojrat Bladi"
    primary_media_url: str = Field(min_length=1, max_length=500)
    additional_media_urls: List[str] = Field(default_factory=list)

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_sku(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("category_code", mode="before")
    @classmethod
    def normalize_category_code(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        normalized = []
        for value in values:
            tag = value.strip()
            if not tag:
                raise ValueError("لا يمكن أن يكون الوسم فارغاً")
            if len(tag) > 50:
                raise ValueError("يجب ألا يتجاوز الوسم 50 حرفاً")
            if tag not in normalized:
                normalized.append(tag)
        return normalized


class ProductCreate(ProductBase):
    factory_id: int


class ProductUpdate(BaseModel):
    category_code: Optional[str] = Field(default=None, min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    tags: Optional[List[str]] = Field(default=None, max_length=30)
    title: Optional[str] = Field(default=None, min_length=2, max_length=255)
    description: Optional[str] = None
    price: Optional[float] = Field(default=None, gt=0)
    availability: Optional[Availability] = None

    @field_validator("category_code", mode="before")
    @classmethod
    def normalize_update_category_code(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("tags")
    @classmethod
    def validate_update_tags(cls, values):
        if values is None:
            return values
        normalized = []
        for value in values:
            tag = value.strip()
            if not tag:
                raise ValueError("لا يمكن أن يكون الوسم فارغاً")
            if len(tag) > 50:
                raise ValueError("يجب ألا يتجاوز الوسم 50 حرفاً")
            if tag not in normalized:
                normalized.append(tag)
        return normalized


class ProductSkuUpdate(ProductUpdate):
    """حقول المنتج التي يمكن تعديلها باستخدام SKU."""
    pass


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
    category_code: str
    tags: List[str] = Field(default_factory=list)
    title: str
    description: Optional[str] = None
    price: float
    currency: str
    availability: Availability
    condition: str
    brand: str
    primary_media_url: str
    additional_media_urls: List[str] = Field(default_factory=list)


class PostBase(BaseModel):
    factory_id: int
    media_type: Literal["image", "gallery", "reel"]
    media_urls: List[str] = Field(min_length=1)
    description: Optional[str] = None
    status: PostStatus = PostStatus.DRAFT
    approval: PostApproval = PostApproval.PENDING
    published_at: Optional[datetime] = None


class PostCreate(PostBase):
    pass


class PostUpdate(BaseModel):
    factory_id: Optional[int] = None
    media_type: Optional[Literal["image", "gallery", "reel"]] = None
    media_urls: Optional[List[str]] = Field(default=None, min_length=1)
    description: Optional[str] = None
    status: Optional[PostStatus] = None
    approval: Optional[PostApproval] = None
    published_at: Optional[datetime] = None


class PostResponse(PostBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
