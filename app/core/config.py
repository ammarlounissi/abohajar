"""كل الإعدادات في مكان واحد. القيم السرية تأتي من ملف .env فقط."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent.parent  # جذر المشروع


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


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
        o.strip()
        for o in _get("ALLOWED_ORIGINS", "https://store.hadjretbladi.com").split(",")
        if o.strip()
    ]

    # الروابط العامة
    BASE_URL = _get("BASE_URL").rstrip("/")             # عنوان هذا الـ API العام
    PRODUCT_LINK_TEMPLATE = _get(
        "PRODUCT_LINK_TEMPLATE", "https://store.hadjretbladi.com/p/{sku}"
    )

    # قاعدة روابط صور المنتجات: {IMAGE_BASE_URL}/{الفئة}/{SKU}/1.jpg
    IMAGE_BASE_URL = _get("IMAGE_BASE_URL").rstrip("/")

    # المجلدات
    STATIC_DIR = BASE_DIR / "static"
    UPLOADS_DIR = STATIC_DIR / "uploads" / "factories"

    REQUIRED = (
        "VERIFY_TOKEN",
        "META_APP_SECRET",
        "META_ACCESS_TOKEN",
        "META_CATALOG_ID",
        "WHATSAPP_PHONE_NUMBER_ID",
        "ADMIN_API_KEY",
        "BASE_URL",
    )

    @property
    def graph_url(self) -> str:
        return f"https://graph.facebook.com/{self.GRAPH_API_VERSION}"

    def validate(self) -> None:
        missing = [n for n in self.REQUIRED if not getattr(self, n)]
        if missing:
            raise RuntimeError(f"متغيرات ناقصة في .env: {', '.join(missing)}")


settings = Settings()
