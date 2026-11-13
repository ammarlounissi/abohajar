import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
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
    kind: str = "supplier"                 # supplier | customer (مساحة زبون لصفحته المربوطة)
    phone_number: Optional[str] = None     # اختياري لمساحات الزبائن


class AIModelSelection(BaseModel):
    model: str = Field(min_length=1, max_length=100, pattern=r"^(?:models/)?[A-Za-z0-9][A-Za-z0-9._:-]*$")

    @field_validator("model", mode="before")
    @classmethod
    def normalize_model(cls, value):
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("models/"):
                value = value[len("models/"):]
        return value


class FactoryChatRequest(AIModelSelection):
    factory_id: int = Field(gt=0)
    model_config = ConfigDict(extra="forbid")


class FactoryReelModelRequest(AIModelSelection):
    model_config = ConfigDict(extra="forbid")


class FactoryReelScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(default="gemini-3.1-flash-lite", min_length=1, max_length=100,
                       pattern=r"^(?:models/)?[A-Za-z0-9][A-Za-z0-9._:-]*$")
    daily_time: time = time(1, 0)
    timezone: str = Field(default="Africa/Algiers", min_length=1, max_length=64)
    start_date: Optional[date] = None
    stop_date: Optional[date] = None
    is_active: bool = True

    @field_validator("model", mode="before")
    @classmethod
    def normalize_schedule_model(cls, value):
        if isinstance(value, str):
            return value.strip().removeprefix("models/")
        return value

    @field_validator("daily_time", mode="before")
    @classmethod
    def minute_precision_only(cls, value):
        if isinstance(value, str) and re.fullmatch(r"\d{2}:\d{2}", value.strip()):
            return value.strip()
        if isinstance(value, time) and value.second == 0 and value.microsecond == 0:
            return value
        raise ValueError("وقت التشغيل يجب أن يكون بصيغة HH:MM")

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("المنطقة الزمنية غير معروفة") from exc
        return value

    @model_validator(mode="after")
    def valid_date_range(self):
        if self.start_date and self.stop_date and self.stop_date < self.start_date:
            raise ValueError("تاريخ التوقف يجب أن يكون في أو بعد تاريخ الانطلاق")
        return self


class FactoryReelScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Optional[str] = Field(default=None, min_length=1, max_length=100,
                                 pattern=r"^(?:models/)?[A-Za-z0-9][A-Za-z0-9._:-]*$")
    daily_time: Optional[time] = None
    timezone: Optional[str] = Field(default=None, min_length=1, max_length=64)
    start_date: Optional[date] = None
    stop_date: Optional[date] = None
    is_active: Optional[bool] = None

    @field_validator("model", mode="before")
    @classmethod
    def normalize_update_model(cls, value):
        return value.strip().removeprefix("models/") if isinstance(value, str) else value

    @field_validator("daily_time", mode="before")
    @classmethod
    def validate_update_time(cls, value):
        if value is None:
            return value
        if isinstance(value, str) and re.fullmatch(r"\d{2}:\d{2}", value.strip()):
            return value.strip()
        if isinstance(value, time) and value.second == 0 and value.microsecond == 0:
            return value
        raise ValueError("وقت التشغيل يجب أن يكون بصيغة HH:MM")

    @field_validator("timezone")
    @classmethod
    def validate_update_timezone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("المنطقة الزمنية غير معروفة") from exc
        return value

    @model_validator(mode="after")
    def valid_update_date_range(self):
        if self.start_date and self.stop_date and self.stop_date < self.start_date:
            raise ValueError("تاريخ التوقف يجب أن يكون في أو بعد تاريخ الانطلاق")
        return self


class FactoryReelScheduleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    factory_id: int
    model: str
    daily_time: str
    timezone: str
    start_date: date
    stop_date: Optional[date] = None
    created_at: datetime
    is_active: bool
    last_run_on: Optional[date] = None
    last_run_at: Optional[datetime] = None
    last_status: Optional[str] = None
    last_error: Optional[str] = None
    last_reel_request_id: Optional[int] = None


class FactoryDescriptionJobAccepted(BaseModel):
    id: int
    factory_id: int
    status: Literal["queued"]
    status_url: str


class FactoryDescriptionJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factory_id: int
    prompt: str
    model: str
    provider: str
    status: Literal["queued", "running", "succeeded", "failed"]
    response: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


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


class FactoryVideoGenerateRequest(BaseModel):
    source: Literal["specific_product", "random_product", "library"] = "random_product"
    sku: Optional[str] = Field(default=None, min_length=2, max_length=50)
    audio: Optional[str] = Field(default=None, min_length=1, max_length=120)
    template: str = Field(default="cinematic_30", min_length=1, max_length=40)
    duration: Optional[float] = Field(default=None, ge=3, le=60)

    @field_validator("sku", mode="before")
    @classmethod
    def normalize_video_sku(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("audio", mode="before")
    @classmethod
    def strip_video_audio(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def validate_source_sku(self):
        if self.source == "specific_product" and not self.sku:
            raise ValueError("رمز SKU مطلوب عند اختيار منتج محدد")
        if self.source != "specific_product" and self.sku is not None:
            raise ValueError("رمز SKU يقبل فقط عند اختيار منتج محدد")
        return self


class FactoryVideoProductOption(BaseModel):
    sku: str
    title: str


class FactoryVideoTemplateOption(BaseModel):
    id: str
    name: str
    duration: float
    max_images: int


class FactoryVideoOptionsResponse(BaseModel):
    factory_id: int
    products: List[FactoryVideoProductOption]
    audio_files: List[str]
    default_audio: Optional[str] = None
    default_template: str
    library_images_count: int
    max_duration: float
    templates: List[FactoryVideoTemplateOption]


class FactoryVideoResponse(BaseModel):
    factory_id: int
    source: Literal["specific_product", "random_product", "library"]
    template: str
    sku: Optional[str] = None
    video_url: str
    size_bytes: int
    duration: float
    images_used: int
    audio: Optional[str] = None


class FactoryVideoJobAccepted(BaseModel):
    id: int
    factory_id: int
    status: Literal["queued"]
    status_url: str


class FactoryVideoJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factory_id: int
    source: Literal["specific_product", "random_product", "library"]
    template: str
    sku: Optional[str] = None
    duration: float
    audio: Optional[str] = None
    status: Literal["queued", "running", "succeeded", "failed"]
    video_url: Optional[str] = None
    size_bytes: Optional[int] = None
    images_used: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class FactoryReelRequestAccepted(BaseModel):
    id: int
    factory_id: int
    video_job_id: int
    chat_job_id: int
    status: Literal["pending"]
    status_url: str


class FactoryReelRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factory_id: int
    video_job_id: int
    chat_job_id: int
    video_status: str
    chat_status: str
    status: Literal["pending", "finalizing", "succeeded", "failed"]
    post_id: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class DriveSyncResponse(BaseModel):
    factory_id: int
    folders: int                 # مجلدات Drive المشارَكة التي تعود لبريد المصنع
    copied: int
    skipped: int                 # غير صالح أو كبير جداً (لا يُعاد تنزيله)
    failed: int                  # فشل مؤقت، يُعاد في التشغيل التالي
    remaining: int               # صور جديدة لم تُعالَج بسبب limit، أعد الاستدعاء لمتابعتها
    errors: List[str] = Field(default_factory=list)


# --- قنوات النشر ---
class SocialChannelCreate(BaseModel):
    factory_id: Optional[int] = Field(default=None, gt=0)
    platform: ChannelPlatform
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    remote_id: str = Field(min_length=1, max_length=100)    # صيغته تُفحص حسب المنصة في الخدمة
    is_active: bool = True
    bot_enabled: bool = True

    @field_validator("name", "description", "remote_id", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v


class SocialChannelUpdate(BaseModel):
    """المنصة لا تتغير بعد الإنشاء (احذف القناة وأنشئ غيرها)."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=1000)
    remote_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    is_active: Optional[bool] = None
    bot_enabled: Optional[bool] = None

    @field_validator("name", "description", "remote_id", mode="before")
    @classmethod
    def strip_text(cls, v):
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _no_null_required(self):
        # PATCH يقبل غياب الحقل لا null صريحاً في أعمدة NOT NULL
        for field in ("name", "description", "remote_id", "is_active", "bot_enabled"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} لا يقبل null")
        return self


class SocialChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    factory_id: Optional[int]
    platform: str
    name: str
    description: str
    remote_id: str
    is_active: bool
    bot_enabled: bool
    created_at: datetime


# --- خدمات Facebook Login ---
class FacebookServicePage(BaseModel):
    """صفحة فيسبوك التي تعمل عليها خدمة المستخدم. بلا أي توكن."""
    page_id: str
    page_name: str
    is_active: bool


class FacebookPageCandidate(BaseModel):
    page_id: str
    page_name: str


class FacebookServiceStatus(BaseModel):
    """حالة خدمة تعمل عبر Facebook Login للمستخدم الحالي.
    page: الصفحة المربوطة (واحدة فقط) أو null. candidates: صفحات تنتظر أن يختار منها واحدة."""
    service: str
    name: str
    scopes: List[str]
    page: Optional[FacebookServicePage] = None
    candidates: List[FacebookPageCandidate] = []


class FacebookPageSelection(BaseModel):
    page_id: str = Field(min_length=1, max_length=100)


# --- مساحة الزبون (مكتبة الصفحة المربوطة بخدمة) ---
class CustomerWorkspaceResponse(BaseModel):
    """ملخص مساحة الزبون لخدمة: صفحتها المربوطة، عدد وسائطها، وحدود الرفع."""
    service: str
    page_id: str
    page_name: str
    images_count: int
    videos_count: int
    max_images: int
    max_image_bytes: int
    image_extensions: List[str]
    image_types: List[str]   # أنواع المحتوى المقبولة (لمنتقي الصور في الهاتف مع الامتدادات)


class CustomerPageLook(BaseModel):
    """الصفحة المربوطة كما تظهر في فيسبوك. الصور روابط فيسبوك الموقّعة (تُعرض مباشرة)، وأي منها فارغ إن تعذّر جلبه.
    cover_offset_y: موضع الغلاف عمودياً كما ضبطه صاحب الصفحة (0 أعلى الصورة، 100 أسفلها)."""
    page_id: str
    name: str
    is_active: bool
    picture_url: Optional[str] = None
    cover_url: Optional[str] = None
    cover_offset_y: int = 50
    followers: Optional[int] = None
    link: Optional[str] = None


class CustomerOverview(BaseModel):
    """رئيسية لوحة الزبون: صفحته، وما ينتظره منه، وما أُنجز.
    missing_settings: ما لم يُكتب في إعدادات الصفحة (activity · whatsapp · hashtags).
    drafts_to_review: مسودات جاهزة تنتظر موافقته؛ drafts_writing: تُكتب نصوصها الآن.
    scheduled: ما وافق عليه ولم يُنشر بعد (يُجهَّز أو ينتظر موعده)، وأقرب موعد next_scheduled_at (UTC).
    published: منشورات الفيديو المنشورة على الصفحة منذ البداية. ما تعذّر نشره ويحتاج تدخله: failed_scheduled من
    المسودات (قسم النشر المجدول)، وfailed_videos من النشر اليدوي (قسم توليد الفيديو).
    timezone: توقيت عرض المواعيد (توقيت النشر)."""
    page: CustomerPageLook
    missing_settings: List[Literal["activity", "whatsapp", "hashtags"]]
    images: int
    max_images: int
    videos: int
    video_generating: bool
    drafts_to_review: int
    drafts_writing: int
    scheduled: int
    next_scheduled_at: Optional[datetime] = None
    published: int
    failed_scheduled: int
    failed_videos: int
    timezone: str
    notifications_available: bool
    notification_devices: int


class CustomerMediaResponse(BaseModel):
    """وسيط في مكتبة صفحة الزبون. بلا رقم المصنع: الزبون لا يتعامل به."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    media_type: str
    url: str
    description: Optional[str] = None


class CustomerVideoTemplateOption(FactoryVideoTemplateOption):
    """القالب مع ما يرسمه به مكوّن Remotion (ProductReel) في المعاينة والرندرة.
    zoom: مقدار التكبير على طول اللقطة (0.08 = 8%). pan: انزلاق أفقي مع التكبير. crossfade: مدة الانتقال بالثواني.
    card: إطار أبيض حول الصورة."""
    zoom: float
    pan: bool
    transition: Literal["fade", "fadeblack", "smoothleft", "wipeleft", "circleopen"]
    crossfade: float
    card: bool


class CustomerVideoOptions(BaseModel):
    """ما يختار منه الزبون قبل توليد فيديو من صور مكتبته."""
    templates: List[CustomerVideoTemplateOption]
    default_template: str
    audio_files: List[str]
    images_count: int
    videos_count: int
    max_videos: int


class CustomerVideoRequest(BaseModel):
    """طلب فيديو من صور المكتبة. بلا قالب: القالب الافتراضي. بلا صوت: صوت عشوائي من المتاح.
    image_ids: صور المكتبة بترتيب المعاينة (حتى max_images للقالب)؛ بدونها تُختار عشوائياً."""
    template: Optional[str] = Field(default=None, min_length=1, max_length=40)
    audio: Optional[str] = Field(default=None, min_length=1, max_length=120)
    image_ids: Optional[List[int]] = Field(default=None, min_length=1, max_length=60)

    @field_validator("template", "audio", mode="before")
    @classmethod
    def strip_text(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value


class CustomerPublishRequest(BaseModel):
    """نص المنشور المرافق للفيديو (اختياري)."""
    caption: str = Field(default="", max_length=2200)


class CustomerVideoPost(BaseModel):
    """حالة نشر فيديو على صفحة الزبون. scheduled: منشور مجدول ينتظر موعده. unknown: بقي قيد النشر أكثر من المهلة ولا نعرف هل نُشر.
    error رسالة للزبون، وerror_detail النص التقني كما جاء (للدعم). media_id فارغ إن حُذف الفيديو من المكتبة."""
    id: int
    media_id: Optional[int] = None
    video_url: str
    caption: str
    status: Literal["scheduled", "publishing", "published", "failed", "unknown"]
    error: Optional[str] = None
    error_detail: Optional[str] = None
    published_at: Optional[datetime] = None
    scheduled_at: Optional[datetime] = None        # للمجدول (scheduled): موعد نشره، UTC
    facebook_url: Optional[str] = None


class CustomerProfile(BaseModel):
    """إعدادات صفحة الزبون، وكلها لمصنع الصفحة (لا للمستخدم). name وactivity يكتب منهما Gemini الوصف؛
    whatsapp (factories.phone_number) وhashtags تُلحق بعده كما هي. activity فارغ إن لم يُكتب ولم يأتِ من فيسبوك.
    admin_managed: صفحة مصنع مورّد، اسمه ورقمه تديرهما الإدارة فلا يُعدَّلان من هنا."""
    name: str
    admin_managed: bool
    activity: str
    whatsapp: Optional[str] = None
    hashtags: List[str]


MAX_PROFILE_HASHTAGS = 10
_HASHTAG_BODY = re.compile(r"^[\w]{2,40}$")


class CustomerProfileUpdate(BaseModel):
    """تعديل جزئي: يُغيَّر ما أُرسل فقط. whatsapp فارغ أو null يحذف الرقم، وhashtags [] تحذف الوسوم.
    whatsapp يقبل المحلي (0550123456 -> +213550123456) والدولي (+213…، 00213…).
    hashtags تقبل الوسم بـ # أو بدونه، والمسافات داخله تصير _."""
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    activity: Optional[str] = Field(default=None, min_length=3, max_length=500)
    whatsapp: Optional[str] = Field(default=None, max_length=20)
    hashtags: Optional[List[str]] = Field(default=None, max_length=MAX_PROFILE_HASHTAGS)

    @field_validator("name", "activity", mode="before")
    @classmethod
    def collapse_spaces(cls, value):
        return " ".join(value.split()) if isinstance(value, str) else value

    @field_validator("whatsapp", mode="before")
    @classmethod
    def normalize_whatsapp(cls, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("رقم واتساب نص")
        raw = re.sub(r"[\s\-().]", "", value)
        if not raw:
            return ""
        if raw.startswith("00"):
            raw = "+" + raw[2:]
        elif re.fullmatch(r"0[5-7]\d{8}", raw):          # رقم جوال جزائري محلي
            raw = "+213" + raw[1:]
        elif not raw.startswith("+"):
            raw = "+" + raw
        if not re.fullmatch(r"\+\d{8,15}", raw):
            raise ValueError("رقم واتساب غير صالح؛ اكتبه مثل 0550123456 أو +213550123456")
        return raw

    @field_validator("hashtags", mode="before")
    @classmethod
    def normalize_hashtags(cls, values):
        if values is None:
            return None
        if not isinstance(values, list):
            raise ValueError("الوسوم قائمة")
        tags = []
        for value in values:
            body = "_".join(str(value).replace("#", " ").split())
            if not body:
                continue
            if not _HASHTAG_BODY.fullmatch(body):
                raise ValueError(f"وسم غير صالح: {value} (حروف وأرقام فقط، من 2 إلى 40)")
            tags.append("#" + body)
        return list(dict.fromkeys(tags))


class CustomerCaptionJob(BaseModel):
    """مهمة كتابة نص منشور بـ Gemini. caption يُملأ عند النجاح، وerror (رسالة للزبون) مع
    error_detail (النص التقني) عند الفشل."""
    id: int
    status: Literal["queued", "running", "succeeded", "failed"]
    caption: Optional[str] = None
    error: Optional[str] = None
    error_detail: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class CustomerVideoJob(BaseModel):
    """مهمة توليد فيديو لصفحة الزبون. video_url يُملأ عند النجاح، وerror عند الفشل."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: Literal["queued", "running", "succeeded", "failed"]
    template: str
    duration: float
    audio: Optional[str] = None
    video_url: Optional[str] = None
    images_used: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


# --- النشر الجماعي المجدول: مسودات تُراجع ويُوافق عليها قبل الرندرة والنشر ---
class CustomerDraftImage(BaseModel):
    id: int
    url: str


class CustomerDraft(BaseModel):
    """مسودة منشور فيديو مجدول، بكل ما سيُنشر كما يُعاين. الأوقات UTC (بلاحقة Z).
    status: writing (Gemini يكتب) · ready (بانتظار المراجعة) · approved (وافق، الفيديو ينتظر دوره قبل الموعد)
    · rendering · scheduled (جاهز وينتظر موعده) · publishing · published · failed · cancelled (تُسترجع).
    images: صور المكتبة بترتيبها (ما حُذف منها لا يظهر). caption_error: تعذّرت كتابة النص فيكتبه الزبون."""
    id: int
    status: Literal[
        "writing", "ready", "approved", "rendering", "scheduled", "publishing", "published", "failed", "cancelled",
    ]
    template: str
    audio: Optional[str] = None
    images: List[CustomerDraftImage]
    caption: str
    caption_error: Optional[str] = None
    scheduled_at: datetime
    planned_at: datetime                        # الموعد الأصلي عند التوليد (للعودة إليه)
    cancelled_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    error: Optional[str] = None
    video_url: Optional[str] = None
    facebook_url: Optional[str] = None
    published_at: Optional[datetime] = None


class CustomerDraftRules(BaseModel):
    """قيود الجدولة كما تطبقها الخلفية، لتعرضها الواجهة وتتحقق بها قبل الإرسال."""
    max_batch: int
    max_open: int
    max_per_day: int
    min_gap_hours: int
    min_lead_minutes: int
    max_ahead_days: int
    timezone: str


class CustomerDrafts(BaseModel):
    """مسودات الصفحة ومنشوراتها المجدولة، الأقرب موعداً أولاً."""
    drafts: List[CustomerDraft]
    rules: CustomerDraftRules


class CustomerDraftsRequest(BaseModel):
    """توليد count مسودة، واحدة كل يوم ابتداءً من الغد في الساعة time (بتوقيت الجزائر)."""
    count: int = Field(default=30, ge=1, le=30)
    time: str = Field(default="20:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class CustomerDraftUpdate(BaseModel):
    """caption: قبل الموافقة فقط. scheduled_at: بمنطقة زمنية (مثل ...Z)، ويُقبل قبل النشر."""
    caption: Optional[str] = Field(default=None, max_length=2200)
    scheduled_at: Optional[datetime] = None

    @field_validator("scheduled_at")
    @classmethod
    def require_timezone(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("الموعد يُرسل بمنطقته الزمنية، مثل 2026-11-20T19:00:00Z")
        return value


class CustomerDraftsApproval(BaseModel):
    """المسودات التي راجعها الزبون ووافق عليها (بأرقامها كما ظهرت له)."""
    ids: List[int] = Field(min_length=1, max_length=60)


# --- إشعارات المتصفح ---
class PushStatus(BaseModel):
    """available=False: المفتاحان غير مضبوطين على الخادم، فتخفي اللوحة زر الإشعارات."""
    available: bool
    public_key: Optional[str] = None          # applicationServerKey للمتصفح (base64url)
    devices: int                              # أجهزة هذه الصفحة المشتركة
    subscribed: bool                          # هل الجهاز المرسَل (endpoint) منها


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class PushSubscriptionIn(BaseModel):
    """ما يعيده PushSubscription.toJSON() في المتصفح."""
    endpoint: str = Field(min_length=1, max_length=1000)
    keys: PushSubscriptionKeys
    expirationTime: Optional[float] = None


class PushEndpoint(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1000)


class PushTestResult(BaseModel):
    sent: bool


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
    video_url: Optional[str] = Field(default=None, max_length=500)

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
    video_url: Optional[str] = None


class ProductVideoResponse(BaseModel):
    sku: str
    video_url: str
    size_bytes: int
    duration: float
    images_used: int
    index: int
    audio: Optional[str] = None


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


class FactoryReelPostRequest(BaseModel):
    """ثلاثة معرّفات فقط: الباقي (الوسائط والوصف والهاتف والوسوم والقنوات) يُستخرج منها."""
    model_config = ConfigDict(extra="forbid")

    factory_id: int = Field(gt=0)
    video_id: int = Field(gt=0, description="معرّف مهمة توليد الفيديو (factory_video_jobs)")
    chat_id: int = Field(gt=0, description="معرّف مهمة الوصف عبر Ollama (factory_description_jobs)")


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
