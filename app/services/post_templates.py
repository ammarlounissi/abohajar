"""قوالب نص المنشور من بيانات المنتج، قالب لكل منصة. دوال نقية بلا قاعدة بيانات ولا شبكة.

    build_caption(product, "facebook")   # بروابط (واتساب + صفحة المنتج)
    build_caption(product, "instagram")  # بلا روابط (لا تُنقر في وصف إنستغرام) ويظهر رمز المنتج بدلها

منصة بلا قالب خاص تأخذ قالب الروابط. قالب جديد = دالة هنا + سطر في TEMPLATES.
القالب يُنتج النص النهائي، ويُحفظ كما نُشر في post_deliveries.caption فلا يتغير بتغير المنتج لاحقاً.
"""
import re
from typing import Callable, Dict, List
from urllib.parse import quote

from app.core.config import settings

BRAND_TAGS = ["#حجرة_بلادي", "#ديكور_جزائري", "#الجزائر"]
MAX_PRODUCT_TAGS = 3


def intl_phone(local: str, country: str = "213") -> str:
    """0549194378 -> 213549194378 (الصيغة التي يقبلها wa.me)."""
    digits = re.sub(r"\D", "", local)
    return digits if digits.startswith(country) else country + digits.lstrip("0")


def whatsapp_link(phone: str, sku: str) -> str:
    return f"https://wa.me/{intl_phone(phone)}?text=" + quote(f"أريد الاستفسار عن المنتج {sku}")


def hashtags(product) -> str:
    tags = list(BRAND_TAGS)
    tags += ["#" + str(t).strip().replace(" ", "_") for t in (product.tags or []) if str(t).strip()][:MAX_PRODUCT_TAGS]
    return " ".join(dict.fromkeys(tags))


def _title(product) -> str:
    name = (product.commercial_name or product.title).strip()
    return name if name.startswith("حجر") else f"حجر {name}"


def _head(product) -> List[str]:
    lines = [f"📌 {_title(product)}"]
    if product.description and product.description.strip():
        lines.append(product.description.strip())
    return lines


def with_links(product, phone: str) -> str:
    """فيسبوك وتيليجرام وغيرهما: الروابط قابلة للنقر."""
    return "\n".join(_head(product) + [
        f"📲 للطلب أو الاستفسار عبر واتساب: {phone}",
        whatsapp_link(phone, product.sku),
        f"🔗 تفاصيل المنتج: {settings.product_link(product.sku)}",
        hashtags(product),
    ])


def instagram(product, phone: str) -> str:
    """إنستغرام: لا روابط (غير قابلة للنقر)، فيُذكر الرقم ورمز المنتج ليذكره الزبون في رسالته."""
    return "\n".join(_head(product) + [
        f"📲 للطلب أو الاستفسار عبر واتساب: {phone}",
        f"🏷️ رمز المنتج: {product.sku}",
        hashtags(product),
    ])


TEMPLATES: Dict[str, Callable] = {"instagram": instagram}


def build_caption(product, platform: str, phone: str = "") -> str:
    """نص المنتج للمنصة. phone الافتراضي من POST_CONTACT_PHONE في الإعدادات."""
    return TEMPLATES.get(platform, with_links)(product, phone or settings.POST_CONTACT_PHONE)
