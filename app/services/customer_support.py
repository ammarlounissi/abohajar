"""منطق الرد على استفسارات الزبائن، مستقل عن Meta وأي قناة أخرى."""
from dataclasses import dataclass
import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Post, PostDelivery, Product

STATIC_REPLIES = {
    "ask_location": "مقرنا: حجرة بلادي - رومبوا 14 مقابل مصنع السكر، أولاد موسى بومردم\n0549194378 واتساب\nhttps://maps.app.goo.gl/KVqWJkAENgQBsYQT8",
    "ask_delivery": "التوصيل إلى 58 ولاية، 400 دج للشمال و600 دج للجنوب، والدفع عند الاستلام.\nواتساب: 0554503568",
    "greeting": "وعليكم السلام، مرحبًا بك عند حجرة بلادي. ما الموديل الذي تهتم به؟",
}

@dataclass(frozen=True)
class SupportReply:
    text: str
    intent: str
    product_sku: Optional[str] = None


def _post_id_candidates(facebook_post_id: str) -> tuple[str, ...]:
    value = (facebook_post_id or "").strip()
    if not value:
        return ()
    short_id = value.rsplit("_", 1)[-1]
    return tuple(dict.fromkeys((value, short_id)))


def get_product_by_facebook_id(db: Session, facebook_post_id: str) -> Optional[Product]:
    """يعثر على المنتج المرتبط بمعرّف منشور Facebook الكامل أو المختصر."""
    for remote_id in _post_id_candidates(facebook_post_id):
        delivery = (
            db.query(PostDelivery)
            .filter(PostDelivery.remote_id == remote_id)
            .first()
        )
        if delivery and delivery.post and delivery.post.sku:
            return db.query(Product).filter(Product.sku == delivery.post.sku).first()
    return None


def detect_intent(text: str) -> str:
    """يحدد نية شائعة من نص عربي أو فرنسي أو إنجليزي."""
    normalized = (text or "").strip().lower()
    groups = {
        "ask_location": ("وين", "أين", "اين", "مقر", "عنوان", "adresse", "مكان", "location"),
        "ask_delivery": ("توصيل", "توصلو", "livraison", "58", "ولاية", "delivery"),
        "ask_stock": ("كاين", "متوفر", "disponible", "موجود", "stock"),
        "ask_size": ("مقاس", "حجم", "dimension", "dimensions", "cm", "متر", "taille"),
        "greeting": ("سلام", "salam", "slm", "bonjour", "hello", "مرحبا"),
        "ask_price": ("شحال", "بقداه", "prix", "سعر", "سومة", "combien", "price", "ثمن"),
    }
    for intent, keywords in groups.items():
        if any(keyword in normalized for keyword in keywords):
            return intent
    return "ask_price"


def _product_by_sku(db: Session, sku: Optional[str]) -> Optional[Product]:
    if not sku:
        return None
    return db.query(Product).filter(Product.sku == sku.strip().upper()).first()


def _sku_from_text(text: str) -> Optional[str]:
    match = re.search(r"\b([A-Z]{1,8}\d{1,8})\b", (text or "").upper())
    return match.group(1) if match else None


def build_reply(
    db: Session,
    *,
    facebook_post_id: Optional[str] = None,
    post_db_id: Optional[int] = None,
    sku: Optional[str] = None,
    text: str = "",
    intent: Optional[str] = None,
) -> SupportReply:
    """يبني ردًا لا يعرف طريقة إرساله ولا القناة التي جاءت منها الرسالة."""
    resolved_intent = intent or detect_intent(text)
    product = get_product_by_facebook_id(db, facebook_post_id) if facebook_post_id else None
    if not product and post_db_id:
        post = db.query(Post).filter(Post.id == post_db_id).first()
        product = _product_by_sku(db, post.sku if post else None)
    if not product:
        product = _product_by_sku(db, sku or _sku_from_text(text))

    if resolved_intent in STATIC_REPLIES:
        return SupportReply(STATIC_REPLIES[resolved_intent], resolved_intent, product.sku if product else None)
    if not product:
        return SupportReply(
            "هذا المنشور قديم وغير مربوط بمنتج. أرسل رمز المنتج مثل B001 أو صورة الموديل لأعطيك السعر والتوفر.",
            resolved_intent,
        )
    if resolved_intent == "ask_stock":
        availability = getattr(product.availability, "value", product.availability) or "متوفر"
        available = "متوفر" if str(availability).lower() in {"in stock", "متوفر"} else "غير متوفر حاليًا"
        text = f"{product.title}: {available}. السعر {product.price:g} دج. الكود {product.sku}."
    elif resolved_intent == "ask_size":
        text = f"{product.title}: المقاس {product.sizes or 'حسب الطلب'}. الكود {product.sku}."
    else:
        text = f"سعر {product.title} هو {product.price:g} دج/م². الكود {product.sku}."
    return SupportReply(text, resolved_intent, product.sku)


def get_reply(db: Session, fb_post_id: Optional[str] = None, post_db_id: Optional[int] = None,
              sku: Optional[str] = None, intent: str = "ask_price") -> str:
    """واجهة توافقية ترجع النص فقط للكود القديم."""
    return build_reply(db, facebook_post_id=fb_post_id, post_db_id=post_db_id, sku=sku, intent=intent).text
