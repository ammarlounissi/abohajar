import re
from datetime import date, datetime, time
from typing import List, Literal, Optional

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.schedule_slots import parse_times
from app.models import Availability, Channel, ChannelPlatform, PostApproval, PostStatus, SyncStatus

SKU_PATTERN = r"^[A-Z0-9][A-Z0-9_-]{1,49}$"   # مثال: STONE-GLM-01


# --- المصانع ---
class FactoryBase(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    activity: str = Field(default="", max_length=500)
    phone_number: str = Field(pattern=r"^\+?\d{8,15}$")   # بصيغة دولية، مثل +213550000000
    address: Optional[str] = Field(default=None, max_length=255)

    @field_validator("activity", mode="before")
    @classmethod
    def strip_activity(cls, value):
        if value is None:
            raise ValueError("activity لا يقبل null")
        return value.strip() if isinstance(value, str) else value


class FactoryCreate(FactoryBase):
    pass


class FactoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    activity: Optional[str] = Field(default=None, max_length=500)
    phone_number: Optional[str] = Field(default=None, pattern=r"^\+?\d{8,15}$")
    address: Optional[str] = Field(default=None, max_length=255)
    is_active: Optional[bool] = None

    @field_validator("activity", mode="before")
    @classmethod
    def strip_activity(cls, value):
        if value is None:
            raise ValueError("activity لا يقبل null")
        return value.strip() if isinstance(value, str) else value


class FactoryResponse(FactoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool


class FactoryChatRequest(BaseModel):
    factory_id: int = Field(gt=0)
    prompt: str = Field(min_length=1, max_length=20000)

    @field_validator("prompt", mode="before")
    @classmethod
    def strip_prompt(cls, value):
        return value.strip() if isinstance(value, str) else value


class FactoryChatResponse(BaseModel):
    id: int
    factory_id: int
    prompt: str
    response: str
    model: str
    created_at: datetime


class FactoryContactCreate(BaseModel):
    channel: Channel
    identifier: str = Field(min_length=1, max_length=100)


class FactoryContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factory_id: int
    channel: str
    identifier: str


class FactoryMediaCreate(BaseModel):
    media_type: Literal["image", "video"]
    url: AnyHttpUrl
    description: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("description", mode="before")
    @classmethod
    def strip_description(cls, value):
        return value.strip() if isinstance(value, str) else value


class FactoryMediaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_type: str
    url: str
    description: Optional[str] = None
    factory_id: int


# --- قنوات النشر ---
class SocialChannelCreate(BaseModel):
    platform: ChannelPlatform
    name: str = Field(min_length=1, max_length=100)
    remote_id: str = Field(min_length=1, max_length=100)    # صيغته تُفحص حسب المنصة في الخدمة
    is_active: bool = True

    @field_validator("name", "remote_id", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v


class SocialChannelUpdate(BaseModel):
    """المنصة لا تتغير بعد الإنشاء (احذف القناة وأنشئ غيرها)."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    remote_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    is_active: Optional[bool] = None

    @field_validator("name", "remote_id", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _no_null_required(self):
        # PATCH يقبل غياب الحقل لا null صريحاً في أعمدة NOT NULL
        for field in ("name", "remote_id", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} لا يقبل null")
        return self


class SocialChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: str
    name: str
    remote_id: str
    is_active: bool
    created_at: datetime


# --- المنتجات ---
class ProductBase(BaseModel):
    sku: str = Field(pattern=SKU_PATTERN)
    category_code: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9_-]+$")
    tags: List[str] = Field(default_factory=list, max_length=30)
    title: str = Field(min_length=2, max_length=255)
    commercial_name: Optional[str] = Field(default=None, max_length=255)
    sizes: Optional[str] = Field(default=None, max_length=255)
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
    commercial_name: Optional[str] = Field(default=None, max_length=255)
    sizes: Optional[str] = Field(default=None, max_length=255)
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
    commercial_name: Optional[str] = None
    sizes: Optional[str] = None
    description: Optional[str] = None
    price: float
    currency: str
    availability: Availability
    condition: str
    brand: str
    primary_media_url: str
    additional_media_urls: List[str] = Field(default_factory=list)


MediaType = Literal["text", "image", "gallery", "reel"]


def _check_media(media_type, media_urls) -> None:
    """منشور النص بلا وسائط، وغيره يحتاج وسيطاً واحداً على الأقل."""
    if media_type == "text" and media_urls:
        raise ValueError("منشور النص لا يحمل وسائط")
    if media_type in ("image", "gallery", "reel") and not media_urls:
        raise ValueError("هذا النوع يحتاج رابط وسائط واحداً على الأقل")


class PostBase(BaseModel):
    factory_id: int
    media_type: MediaType
    media_urls: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    status: PostStatus = PostStatus.DRAFT
    approval: PostApproval = PostApproval.PENDING
    published_at: Optional[datetime] = None
    scheduled_at: Optional[datetime] = None
    sku: Optional[str] = Field(default=None, max_length=50)


class PostCreate(PostBase):
    # معرّفات القنوات من social_channels (فارغة = منشور بلا وجهات، يلزمه تحديد قنوات قبل الاعتماد/النشر)
    channel_ids: List[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _media_consistent(self):
        _check_media(self.media_type, self.media_urls)
        return self


class PostUpdate(BaseModel):
    channel_ids: Optional[List[int]] = None     # يستبدل قنوات المنشور (الناجحة منها تبقى كسجل)
    factory_id: Optional[int] = None
    media_type: Optional[MediaType] = None
    media_urls: Optional[List[str]] = None
    description: Optional[str] = None
    status: Optional[PostStatus] = None
    approval: Optional[PostApproval] = None
    published_at: Optional[datetime] = None
    scheduled_at: Optional[datetime] = None
    sku: Optional[str] = Field(default=None, max_length=50)


class PreparedPostsRequest(BaseModel):
    factory_id: int = Field(gt=0)
    channel_ids: List[int] = Field(min_length=1)
    # ساعات النشر اليومية بتوقيت الجزائر: [8, 14, 18] أو "8,14,18" أو ["08:30"]. المنشورات تُوزَّع عليها يوماً بعد يوم.
    times: Optional[List[time]] = None
    start_date: Optional[date] = None        # أول يوم (محلي) للبدء؛ الافتراضي اليوم، وما فات من مواعيده يُتخطى

    @field_validator("channel_ids")
    @classmethod
    def validate_channel_ids(cls, values):
        if any(channel_id <= 0 for channel_id in values):
            raise ValueError("معرّف القناة يجب أن يكون موجباً")
        return list(dict.fromkeys(values))

    @field_validator("times", mode="before")
    @classmethod
    def validate_times(cls, values):
        return None if values is None else parse_times(values)

    @model_validator(mode="after")
    def _start_date_needs_times(self):
        if self.start_date and not self.times:
            raise ValueError("start_date يحتاج times")
        return self


class _ScheduledPostsRequest(PreparedPostsRequest):
    """طلبات الدفعات الكبيرة (كل المنتجات/تصنيف): times إلزامي كي لا تُنشر الدفعة كلها فوراً."""

    @model_validator(mode="after")
    def _require_times(self):
        if not self.times:
            raise ValueError("times مطلوب، مثل [8, 14, 18] (ساعات النشر اليومية بتوقيت الجزائر)")
        return self


class PreparedPostsAllRequest(_ScheduledPostsRequest):
    pass


class PreparedPostsBySkuRequest(PreparedPostsRequest):
    skus: List[str] = Field(min_length=1)

    @field_validator("skus", mode="before")
    @classmethod
    def normalize_skus(cls, values):
        return [value.strip().upper() if isinstance(value, str) else value for value in values]

    @field_validator("skus")
    @classmethod
    def validate_skus(cls, values):
        if any(not isinstance(sku, str) or not re.fullmatch(SKU_PATTERN, sku) for sku in values):
            raise ValueError("رمز SKU غير صالح")
        return list(dict.fromkeys(values))


class PreparedPostsByCategoryRequest(_ScheduledPostsRequest):
    category_codes: List[str] = Field(min_length=1)

    @field_validator("category_codes", mode="before")
    @classmethod
    def normalize_category_codes(cls, values):
        return [value.strip().upper() if isinstance(value, str) else value for value in values]

    @field_validator("category_codes")
    @classmethod
    def validate_category_codes(cls, values):
        if any(not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9_-]{1,20}", code) for code in values):
            raise ValueError("رمز التصنيف غير صالح")
        return list(dict.fromkeys(values))


class PreparedPostsResponse(BaseModel):
    status: str = "success"
    count: int
    post_ids: List[int]
    first_scheduled_at: Optional[datetime] = None    # UTC، كبقية حقول التاريخ
    last_scheduled_at: Optional[datetime] = None


class PostDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    channel_id: int
    platform: str                    # من القناة
    channel_name: str
    channel_remote_id: str
    caption: Optional[str] = None
    status: str
    remote_id: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    published_at: Optional[datetime] = None


class PostResponse(PostBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    approved_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    error: Optional[str] = None
    deliveries: List[PostDeliveryResponse] = Field(default_factory=list)
