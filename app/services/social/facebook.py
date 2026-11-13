"""النشر على صفحة فيسبوك (نص، صورة، فيديو) برمز الصفحة.

رمز الصفحة من أحد مصدرين:
- رمز القناة المحفوظ معها (channel_token): صفحة ربطها زبون عبر Facebook Login. رمز الإدارة لا يصل إليها.
- رمز مستخدم النظام (META_ACCESS_TOKEN) يُستخرج به رمز الصفحة: صفحات الإدارة.
إن وُجد رمز القناة يُجرَّب أولاً، وإن رفضه فيسبوك كرمز غير صالح يُجرَّب رمز النظام مرة واحدة
(صفحة للإدارة حُفظ معها رمز قديم).

غلاف الريل: إن حمل المنشور رابطاً ثانياً بعد الفيديو (media_urls[1]) فهو صورة الغلاف، تُنزَّل وتُحوَّل إلى JPEG
وتُرفع مع الفيديو في الحقل thumb. تعذّر تنزيلها، أو رفض فيسبوك الطلب بسببها، لا يمنع النشر: يُنشر بلا غلاف."""
import asyncio
import io
import logging
from typing import Optional

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.models import ReportKind
from app.services.social.base import Publisher, PublishError, is_http_url
from app.services.social.graph import graph_call, make_client

logger = logging.getLogger(__name__)

COVER_MAX_BYTES = 15 * 1024 * 1024
COVER_MAX_SIDE = 1920


def _cover_jpeg(content: bytes) -> bytes:
    """صورة الغلاف بصيغة JPEG، باتجاهها الصحيح، وأطول ضلع فيها COVER_MAX_SIDE على الأكثر."""
    with Image.open(io.BytesIO(content)) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((COVER_MAX_SIDE, COVER_MAX_SIDE))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=90)
        return out.getvalue()


async def fetch_cover(client: httpx.AsyncClient, url: str) -> Optional[bytes]:
    """غلاف الريل جاهزاً للرفع، أو None إن تعذّر (يُنشر الفيديو بلا غلاف)."""
    if not is_http_url(url):
        return None
    try:
        response = await client.get(url, follow_redirects=True)
        if response.status_code >= 400 or len(response.content) > COVER_MAX_BYTES:
            logger.warning("cover skipped: %s (http %s, %d bytes)", url, response.status_code, len(response.content))
            return None
        return await asyncio.to_thread(_cover_jpeg, response.content)
    except (httpx.HTTPError, UnidentifiedImageError, OSError) as exc:
        logger.warning("cover skipped: %s (%s)", url, exc)
        return None


class FacebookPublisher(Publisher):
    platform = "facebook"
    VIDEO_TIMEOUT = 180.0

    def __init__(self, transport=None):
        self._transport = transport        # للاختبار (httpx.MockTransport)
        self._page_token: Optional[str] = None

    @property
    def _page_id(self) -> str:
        return self.channel_id or settings.META_PAGE_ID

    def for_channel(self, channel_id: str, token: Optional[str] = None) -> "FacebookPublisher":
        clone = super().for_channel(channel_id, token)
        clone._page_token = None      # رمز كل صفحة مختلف، فلا يُورَّث من نسخة أخرى
        return clone

    def validate(self, post) -> Optional[PublishError]:
        if not self.channel_token and not settings.META_ACCESS_TOKEN:
            return PublishError(ReportKind.TOKEN, "config_missing:META_ACCESS_TOKEN")
        if not self._page_id:
            return PublishError(ReportKind.UNKNOWN, "config_missing:META_PAGE_ID")
        media_type, urls = post.media_type, list(post.media_urls or [])
        if media_type == "gallery":
            return PublishError(ReportKind.MEDIA, "gallery_not_supported_in_phase1")
        if media_type == "text":
            if not (post.description or "").strip():
                return PublishError(ReportKind.MEDIA, "facebook_text_post_needs_caption")
            return None
        if not urls:
            return PublishError(ReportKind.MEDIA, "media_urls_empty")
        if not is_http_url(urls[0]):
            return PublishError(ReportKind.MEDIA, "media_url_not_http")
        return None

    async def _get_page_token(self, client) -> str:
        """رمز الصفحة المستخرج برمز مستخدم النظام (يُحفظ في النسخة لبقية التشغيل)."""
        if not settings.META_ACCESS_TOKEN:
            raise PublishError(ReportKind.TOKEN, "config_missing:META_ACCESS_TOKEN")
        if not self._page_token:
            data = await graph_call(
                client, "GET", self._page_id,
                token=settings.META_ACCESS_TOKEN, params={"fields": "access_token"},
            )
            if not data.get("access_token"):
                raise PublishError(ReportKind.TOKEN, "page_access_token_unavailable")
            self._page_token = data["access_token"]
        return self._page_token

    async def _send(self, client, post, token: str) -> dict:
        caption = post.description or ""
        page = self._page_id
        if post.media_type == "text":
            return await graph_call(client, "POST", f"{page}/feed", token=token, data={"message": caption})
        if post.media_type == "image":
            return await graph_call(
                client, "POST", f"{page}/photos", token=token,
                data={"url": post.media_urls[0], "caption": caption},
            )
        data = {"file_url": post.media_urls[0], "description": caption}   # reel
        cover = await fetch_cover(client, post.media_urls[1]) if len(post.media_urls) > 1 else None
        if cover is not None:
            try:
                return await graph_call(
                    client, "POST", f"{page}/videos", token=token, data=data,
                    files={"thumb": ("cover.jpg", cover, "image/jpeg")},
                )
            except PublishError as exc:
                # الشبكة والرمز وحد الطلبات ليست من الغلاف: يُعاد الخطأ كما هو. غير ذلك قد يكون رفضاً للغلاف،
                # فلا ينشأ الفيديو، ويُعاد الطلب بلا غلاف حتى لا يفوت المنشور موعده.
                if exc.kind in (ReportKind.NETWORK, ReportKind.TOKEN, ReportKind.LIMIT):
                    raise
                logger.warning("reel with cover rejected, retrying without it: %s", exc)
        return await graph_call(client, "POST", f"{page}/videos", token=token, data=data)

    async def _publish(self, post) -> str:
        timeout = self.VIDEO_TIMEOUT if post.media_type == "reel" else 60.0
        async with make_client(timeout, self._transport) as client:
            if self.channel_token:
                try:
                    body = await self._send(client, post, self.channel_token)
                except PublishError as channel_error:
                    if channel_error.kind != ReportKind.TOKEN or not settings.META_ACCESS_TOKEN:
                        raise
                    try:
                        body = await self._send(client, post, await self._get_page_token(client))
                    except PublishError:
                        raise channel_error from None   # رمز النظام لا يصل إلى الصفحة: الخطأ خطأ رمز القناة
            else:
                body = await self._send(client, post, await self._get_page_token(client))
        remote_id = body.get("post_id") or body.get("id")
        if not remote_id:
            raise PublishError(ReportKind.UNKNOWN, f"no_id_in_response:{body}"[:300])
        return str(remote_id)
