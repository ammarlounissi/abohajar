"""محوّلات النشر الاجتماعي. منصة جديدة = ملف محوّل + سطر هنا، دون لمس غيره."""
from typing import Dict

from app.services.social.base import Publisher, PublishError, PublishResult
from app.services.social.facebook import FacebookPublisher
from app.services.social.instagram import InstagramPublisher

REGISTRY = {
    FacebookPublisher.platform: FacebookPublisher,
    InstagramPublisher.platform: InstagramPublisher,
}


def build_publishers() -> Dict[str, Publisher]:
    """نسخة جديدة من كل محوّل لكل تشغيل (رمز الصفحة يُجلب مرة لكل تشغيل)."""
    return {name: cls() for name, cls in REGISTRY.items()}


__all__ = ["Publisher", "PublishError", "PublishResult", "REGISTRY", "build_publishers"]
