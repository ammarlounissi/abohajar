"""واجهة توافقية؛ منطق الرد الفعلي موجود في ``app.services.customer_support``."""
from app.services.customer_support import (
    STATIC_REPLIES,
    SupportReply,
    build_reply,
    detect_intent,
    get_product_by_facebook_id,
    get_reply,
)

__all__ = [
    "STATIC_REPLIES",
    "SupportReply",
    "build_reply",
    "detect_intent",
    "get_product_by_facebook_id",
    "get_reply",
]
