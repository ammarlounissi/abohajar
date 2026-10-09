"""كل الإعدادات في مكان واحد. القيم السرية تأتي من ملف .env فقط."""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # جذر المشروع


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str):
    """رقم صحيح اختياري من .env؛ يرجع None إن كان فارغاً، ويتوقف برسالة واضحة إن لم يكن رقماً."""
    value = _get(name)
    if not value:
        return None
    if not value.isascii() or not value.isdigit():
        raise RuntimeError(f"{name} في .env يجب أن يكون رقماً صحيحاً، وجدنا: {value!r}")
    return int(value)


class Settings:
    DATABASE_URL = _get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'hojrat_bladi.db'}")

    # Meta / WhatsApp
    VERIFY_TOKEN = _get("VERIFY_TOKEN")
    META_APP_SECRET = _get("META_APP_SECRET")          # للتحقق من توقيع الـ webhook
    META_ACCESS_TOKEN = _get("META_ACCESS_TOKEN")
    META_CATALOG_ID = _get("META_CATALOG_ID")
    WHATSAPP_PHONE_NUMBER_ID = _get("WHATSAPP_PHONE_NUMBER_ID")
    GRAPH_API_VERSION = _get("GRAPH_API_VERSION", "v19.0")

    # الأمان
    ADMIN_API_KEY = _get("ADMIN_API_KEY")
    ALLOWED_ORIGINS = [
    o.strip( )
    for o in _get(
        "ALLOWED_ORIGINS",
        "https://hadjretbladi.com,https://www.hadjretbladi.com,https://store.hadjretbladi.com",
     ).split(",")
    if o.strip()
]

    # الروابط العامة
    BASE_URL = _get("BASE_URL").rstrip("/")             # عنوان هذا الـ API العام
    PRODUCT_LINK_TEMPLATE = _get(
        "PRODUCT_LINK_TEMPLATE", "https://store.hadjretbladi.com/{sku}"
    )

    # قاعدة روابط صور المنتجات: {IMAGE_BASE_URL}/{SKU}/{الرقم}.{الامتداد}  (مثل https://hadjretbladi.com/B001/1.jpg)
    IMAGE_BASE_URL = _get("IMAGE_BASE_URL").rstrip("/")

    # مجلد صور المنتجات: مستقل عن كود المشروع، وتخدمه Nginx مباشرة.
    # البنية: {MEDIA_ROOT}/{الفئة}/{SKU}/{الرقم}.{الامتداد}  مثل  B/B001/1.jpg
    MEDIA_ROOT = Path(_get("MEDIA_ROOT")).expanduser() if _get("MEDIA_ROOT") else None
    # أين تُنقل الصورة القديمة عند استبدالها (خارج MEDIA_ROOT كي لا تُزامَن ولا تُخدم)
    MEDIA_BACKUP_DIR = (
        Path(_get("MEDIA_BACKUP_DIR")).expanduser()
        if _get("MEDIA_BACKUP_DIR") else BASE_DIR / "media_backup"
    )

    # ملف CSV (الأعمدة sku,title,price) الذي يقرأه سكريبت الاستيراد التلقائي.
    # إن تُرك فارغاً يُبحث عنه في MEDIA_ROOT/products.csv. الأفضل وضعه خارج MEDIA_ROOT
    # لأن Nginx يخدم كل ما بداخله علناً.
    PRODUCTS_CSV_PATH = (
        Path(_get("PRODUCTS_CSV_PATH")).expanduser() if _get("PRODUCTS_CSV_PATH") else None
    )
    # رقم (id) المصنع الذي تُنسب إليه المنتجات الجديدة عند الاستيراد التلقائي
    DEFAULT_FACTORY_ID = _get_int("DEFAULT_FACTORY_ID")

    # أمر واتساب الإداري: من أرسل هذه الكلمة (نصاً كاملاً) إلى بوت المصنع يُنفَّذ الاستيراد التلقائي فوراً
    # بلا تأكيد. فارغة = الميزة معطّلة. تُولَّد عشوائية (انظر .env.example) ولا تُستعمل في مكان آخر.
    IMPORT_PASSWORD = _get("IMPORT_PASSWORD")
    # اختياري: أرقام (بصيغة دولية، مفصولة بفواصل) المسموح لها بالأمر. فارغ = يكفي صحة الكلمة.
    ADMIN_PHONE_NUMBERS = [
        d for d in (re.sub(r"\D", "", n) for n in _get("ADMIN_PHONE_NUMBERS").split(",")) if d
    ]

    # المجلدات
    STATIC_DIR = BASE_DIR / "static"
    UPLOADS_DIR = STATIC_DIR / "uploads" / "factories"   # قديم: لم يعد يُستعمل لصور المنتجات

    REQUIRED = (
        "VERIFY_TOKEN",
        "META_APP_SECRET",
        "META_ACCESS_TOKEN",
        "META_CATALOG_ID",
        "WHATSAPP_PHONE_NUMBER_ID",
        "ADMIN_API_KEY",
        "BASE_URL",
        "IMAGE_BASE_URL",
        "MEDIA_ROOT",
    )

    @property
    def graph_url(self) -> str:
        return f"https://graph.facebook.com/{self.GRAPH_API_VERSION}"

    def validate(self) -> None:
        missing = [n for n in self.REQUIRED if not getattr(self, n)]
        if missing:
            raise RuntimeError(f"متغيرات ناقصة في .env: {', '.join(missing)}")
        # لا ننشئ MEDIA_ROOT تلقائياً: لو كان المسار خاطئاً أو القرص غير مركّب
        # فسنكتب الصور في مكان خاطئ بصمت. الأفضل أن يتوقف التشغيل.
        if not self.MEDIA_ROOT.is_dir():
            raise RuntimeError(f"MEDIA_ROOT غير موجود أو ليس مجلداً: {self.MEDIA_ROOT}")
        if not os.access(self.MEDIA_ROOT, os.W_OK | os.X_OK):
            raise RuntimeError(f"لا توجد صلاحية كتابة على MEDIA_ROOT: {self.MEDIA_ROOT}")
        # الكلمة تعمل من أي رقم، فالقصيرة أو المعتادة خطر حقيقي
        if self.IMPORT_PASSWORD and len(self.IMPORT_PASSWORD) < 12:
            raise RuntimeError("IMPORT_PASSWORD قصيرة: يجب ألا تقل عن 12 حرفاً")


settings = Settings()
