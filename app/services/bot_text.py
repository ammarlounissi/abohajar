"""دوال نصية صافية لشجرة بوت المصنع (بلا قاعدة بيانات ولا شبكة، فهي سهلة الاختبار)."""
import re
from typing import Optional

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_PRICE_NOISE = re.compile(r"(دج|دينار|da|dzd|د\.ج)", re.IGNORECASE)

CANCEL_WORDS = {"إلغاء", "الغاء", "ألغ", "cancel"}
MENU_WORDS = {"القائمة", "قائمة", "menu"}
BACK_WORDS = {"رجوع", "ارجع", "back"}

MAX_PRICE = 100_000_000


def ascii_digits(text: str) -> str:
    return (text or "").translate(_ARABIC_DIGITS)


def norm_command(text: str) -> str:
    return (text or "").strip().strip(".!؟?").lower()


def parse_price(text: str) -> Optional[float]:
    """'3200' / '3 200' / '٣٢٠٠' / '3200,50' / '3200 دج'  ->  float أو None إن لم يكن رقماً صالحاً."""
    cleaned = _PRICE_NOISE.sub("", ascii_digits(text)).replace(" ", "").replace(",", ".")
    if not re.fullmatch(r"[0-9]+(\.[0-9]{1,2})?", cleaned):
        return None
    value = float(cleaned)
    return value if 0 < value <= MAX_PRICE else None


def parse_index(text: str, maximum: int) -> Optional[int]:
    """رقم صحيح بين 1 وmaximum (يقبل الأرقام العربية)، أو None."""
    cleaned = ascii_digits(text).strip()
    if not re.fullmatch(r"[0-9]{1,3}", cleaned):
        return None
    value = int(cleaned)
    return value if 1 <= value <= maximum else None


def clean_title(text: str) -> Optional[str]:
    value = " ".join((text or "").split())
    return value if 2 <= len(value) <= 255 else None


def clean_description(text: str) -> Optional[str]:
    value = (text or "").strip()
    return value if 1 <= len(value) <= 1500 else None


def next_number(existing_skus, category: str) -> int:
    """أكبر رقم مستعمل في الصنف + 1 (SKU مثل B013 مع الصنف B تعطي 14)."""
    pattern = re.compile(rf"{re.escape(category)}([0-9]+)")
    numbers = [int(m.group(1)) for s in existing_skus if (m := pattern.fullmatch(s or ""))]
    return max(numbers, default=0) + 1


def format_sku(category: str, number: int) -> str:
    return f"{category}{number:03d}"
