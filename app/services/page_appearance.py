"""مظهر صفحة فيسبوك كما يراه زوارها: صورة الغلاف وصورة البروفيل وعدد المتابعين. لا تعرف شيئاً عن HTTP.

يُجلب بتوكن الصفحة ويُحفظ في الذاكرة ساعة (روابط صور فيسبوك موقّعة وتعيش أياماً)، فلا تسأل كل زيارة
للوحة فيسبوك. الجلب مساعدة لا شرط: أي فشل يرجع مظهراً فارغاً وتعرض اللوحة بدائلها."""
import logging
import threading
import time
from typing import Optional

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# picture بمقاس صريح أوضح من type(large) على الشاشات الكثيفة. cover.offset_y نسبة مئوية (0..100) لموضع الصورة عمودياً.
PAGE_FIELDS = "picture.width(320).height(320){url},cover{source,offset_y},followers_count,fan_count,link"
FACEBOOK_TIMEOUT = 8.0
CACHE_SECONDS = 3600
FAILURE_CACHE_SECONDS = 300           # فشل الجلب لا يُعاد في كل طلب
EMPTY = {"picture_url": None, "cover_url": None, "cover_offset_y": 50, "followers": None, "link": None}

_cache: dict = {}
_lock = threading.Lock()


def _parse(body: dict) -> dict:
    picture = ((body.get("picture") or {}).get("data") or {}).get("url")
    cover = body.get("cover") or {}
    offset = cover.get("offset_y")
    followers = body.get("followers_count", body.get("fan_count"))
    return {
        "picture_url": picture if isinstance(picture, str) and picture.startswith("https://") else None,
        "cover_url": cover.get("source") if str(cover.get("source") or "").startswith("https://") else None,
        "cover_offset_y": min(max(int(offset), 0), 100) if isinstance(offset, (int, float)) else 50,
        "followers": followers if isinstance(followers, int) and followers >= 0 else None,
        "link": body.get("link") if str(body.get("link") or "").startswith("https://") else None,
    }


def fetch_page_appearance(
    page_id: str, token: str, *, transport: Optional[httpx.BaseTransport] = None,
) -> Optional[dict]:
    """المظهر من فيسبوك، أو None عند أي فشل (شبكة، توكن منتهٍ، حقل محجوب)."""
    try:
        with httpx.Client(timeout=FACEBOOK_TIMEOUT, transport=transport) as client:
            response = client.get(
                f"{settings.graph_url}/{page_id}", params={"fields": PAGE_FIELDS},
                headers={"Authorization": f"Bearer {token}"},
            )
        body = response.json() if response.content else {}
        if response.status_code >= 400 or not isinstance(body, dict) or body.get("error"):
            logger.warning("Facebook page appearance request failed with HTTP %s", response.status_code)
            return None
        return _parse(body)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Could not fetch Facebook page appearance: %s", type(exc).__name__)
        return None


def page_appearance(page_id: str, token: Optional[str], *, now: Optional[float] = None) -> dict:
    """المظهر المحفوظ إن لم تمضِ مدته، وإلا يُجلب من جديد. بلا توكن: مظهر فارغ."""
    if not token:
        return dict(EMPTY)
    moment = time.monotonic() if now is None else now
    with _lock:
        cached = _cache.get(page_id)
    if cached and cached[0] > moment:
        return dict(cached[1])
    fetched = fetch_page_appearance(page_id, token)
    value = fetched or dict(EMPTY)
    with _lock:
        _cache[page_id] = (moment + (CACHE_SECONDS if fetched else FAILURE_CACHE_SECONDS), value)
    return dict(value)


def forget(page_id: Optional[str] = None) -> None:
    """يمسح المحفوظ (لصفحة أو للكل): بعد إعادة الربط، وفي الاختبارات."""
    with _lock:
        if page_id is None:
            _cache.clear()
        else:
            _cache.pop(page_id, None)
