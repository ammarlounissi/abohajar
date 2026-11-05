"""
أدابتر ميسنجر - يترجم رسالة فيسبوك -> المنطق المعزول
ما فيهش منطق، غير ترجمة
"""
import requests
import os
from typing import Optional
from sqlalchemy.orm import Session
from app.core.logic import get_reply, get_product_by_facebook_id

PAGE_TOKEN = os.getenv("PAGE_TOKEN") or os.getenv("META_PAGE_TOKEN")
PAGE_ID = os.getenv("PAGE_ID", "252638298490671")

def send_private_reply(comment_id: str, message: str) -> dict:
    """يرد بـ private_replies (اللي خدمته من قبل)"""
    url = f"https://graph.facebook.com/v21.0/{comment_id}/private_replies"
    r = requests.post(url, json={"message": message, "access_token": PAGE_TOKEN})
    return r.json()

def send_public_reply(comment_id: str, message: str) -> dict:
    """رد عام"""
    url = f"https://graph.facebook.com/v21.0/{comment_id}/replies"
    r = requests.post(url, json={"message": message, "access_token": PAGE_TOKEN})
    return r.json()

def detect_intent(text: str) -> str:
    """كشف النية بسيط - من بعد نبدلوه بـ Wit.ai"""
    t = text.lower()
    if any(k in t for k in ["شحال", "بقداه", "prix", "سعر", "سومة", "combien", "price", "ثمن"]):
        return "ask_price"
    if any(k in t for k in ["وين", "اين", "مقر", "عنوان", "adresse", "مكان", "location"]):
        return "ask_location"
    if any(k in t for k in ["توصيل", "توصلو", "livraison", "58", "ولاية"]):
        return "ask_delivery"
    if any(k in t for k in ["كاين", "متوفر", "disponible", "موجود"]):
        return "ask_stock"
    if any(k in t for k in ["مقاس", "حجم", "dimension", "cm", "متر"]):
        return "ask_size"
    if any(k in t for k in ["سلام", "salam", "slm", "bonjour", "hello"]):
        return "greeting"
    return "ask_price"  # افتراضي

def handle_facebook_comment(db: Session, fb_post_id: str, comment_id: str, comment_text: str, is_tester: bool = False):
    """
    fb_post_id: 252638298490671_1599567895516202 (من webhook)
    comment_id: id تاع التعليق
    comment_text: نص التعليق
    """
    intent = detect_intent(comment_text)
    
    # عيط للمنطق المعزول (ما يعرفش فيسبوك)
    reply_text = get_reply(db=db, fb_post_id=fb_post_id, intent=intent)
    
    # ابعث الرد
    # اذا تيستر ولا تعليق من زبون حقيقي، استعمل private_replies
    try:
        result = send_private_reply(comment_id, reply_text)
        if "error" in result and result["error"]["code"] == 100:
            # اذا فشل private (مثلا تعلق كصفحة)، جرب public
            result = send_public_reply(comment_id, reply_text)
        return result
    except Exception as e:
        return {"error": str(e), "reply": reply_text}