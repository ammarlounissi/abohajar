"""
المنطق المشترك - معزول عن فيسبوك/انستغرام/تيليجرام
يتعامل مع قاعدة البيانات فقط
المصدر: تحليل 14,844 رسالة حقيقية + 64,685 رسالة كلية
الربط: post_deliveries.remote_id -> posts.sku -> products.price
"""
from typing import Optional
from sqlalchemy.orm import Session

# حاول نجيب المودلز، اذا الأسماء مختلفة بدلوها
try:
    from app.models import PostDelivery, Post, Product
except ImportError:
    # fallback اذا الأسماء مختلفة
    from app.models import post_deliveries as PostDelivery, posts as Post, products as Product

STATIC_REPLIES = {
    "ask_location": "مقرنا: حجرة بلادي - رومبوا 14 مقابل مصنع السكر، أولاد موسى بومرداس\n0549194378 واتساب\nhttps://maps.app.goo.gl/KVqWJkAENgQBsYQT8",
    "ask_delivery": "التوصيل 58 ولاية، 400 دج للشمال و 600 دج للجنوب، الدفع عند الاستلام\nواتساب 0554503568",
    "greeting": "وعليكم السلام، مرحبا بيك عند حجرة بلادي، واش من موديل راك مهتم بيه؟",
}

def get_product_by_facebook_id(db: Session, fb_post_id: str) -> Optional[Product]:
    """
    fb_post_id: 252638298490671_1599567895516202 ولا 1454088080025606
    ترجع Product ولا None
    """
    if not fb_post_id:
        return None
    
    # الجزء الأخير بعد _ هو المهم
    short_id = fb_post_id.split("_")[-1] if "_" in fb_post_id else fb_post_id
    
    # 1. حاول بالمعرف الكامل
    delivery = db.query(PostDelivery).filter(
        PostDelivery.remote_id == fb_post_id
    ).first()
    
    # 2. حاول بالجزء القصير
    if not delivery:
        delivery = db.query(PostDelivery).filter(
            PostDelivery.remote_id == short_id
        ).first()
    
    # 3. حاول بـ LIKE (لأن عندك 252638298490671_1599567895516202 و 1599567895516202)
    if not delivery:
        delivery = db.query(PostDelivery).filter(
            PostDelivery.remote_id.like(f"%{short_id}%")
        ).first()
    
    if not delivery:
        return None
    
    # جيب post
    post = db.query(Post).filter(Post.id == delivery.post_id).first()
    if not post or not post.sku:
        return None
    
    # جيب product
    product = db.query(Product).filter(Product.sku == post.sku).first()
    return product

def get_reply(db: Session, fb_post_id: Optional[str] = None, post_db_id: Optional[int] = None, sku: Optional[str] = None, intent: str = "ask_price") -> str:
    """
    الجدول تاع المنطق المشترك:
    - مربوط: عندك sku -> نعطي السعر من products
    - غير مربوط: ما عندكش sku -> نطلب الكود
    - خليط: ريلز فيه بزاف sku -> نعطي قائمة (مستقبلا)
    """
    product = None
    
    # الحالة 1: جاي من فيسبوك
    if fb_post_id:
        product = get_product_by_facebook_id(db, fb_post_id)
        if product:
            sku = product.sku
    
    # الحالة 2: عندك post_db_id
    if post_db_id and not product:
        post = db.query(Post).filter(Post.id == post_db_id).first()
        if post and post.sku:
            sku = post.sku
            product = db.query(Product).filter(Product.sku == sku).first()
    
    # الحالة 3: عندك sku مباشرة
    if sku and not product:
        product = db.query(Product).filter(Product.sku == sku).first()
    
    # --- المنطق ---
    if intent in STATIC_REPLIES:
        return STATIC_REPLIES[intent]
    
    if intent in ["ask_price", "ask_stock", "ask_size"]:
        if product:
            # منشور مسجل ومربوط ✅ - هذا اللي جربناه وخرج "السهل 6900 دج"
            if intent == "ask_price":
                return f"سعر {product.title} هو {product.price} دج /م² - الكود {product.sku}"
            elif intent == "ask_stock":
                return f"{product.title} متوفر - الكود {product.sku} - السعر {product.price} دج"
            elif intent == "ask_size":
                sizes = getattr(product, 'sizes', None) or "حسب الطلب"
                return f"{product.title} المقاس: {sizes} - الكود {product.sku}"
        else:
            # منشور مسجل وغير مربوط + غير موجود
            return "هذا المنشور قديم غير مربوط، ابعثلي الكود M00X ولا صورة الموديل نعطيك السعر والتوفر"
    
    return "مرحبا، ابعثلي الكود تاع المنتج M00X ولا تواصل واتساب 0554503568"