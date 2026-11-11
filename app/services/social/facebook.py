"""النشر على صفحة فيسبوك (نص، صورة، فيديو) برمز الصفحة.

رمز الصفحة من أحد مصدرين:
- رمز القناة المحفوظ معها (channel_token): صفحة ربطها زبون عبر Facebook Login. رمز الإدارة لا يصل إليها.
- رمز مستخدم النظام (META_ACCESS_TOKEN) يُستخرج به رمز الصفحة: صفحات الإدارة.
إن وُجد رمز القناة يُجرَّب أولاً، وإن رفضه فيسبوك كرمز غير صالح يُجرَّب رمز النظام مرة واحدة
(صفحة للإدارة حُفظ معها رمز قديم)."""
from typing import Optional

from app.core.config import settings
from app.models import ReportKind
from app.services.social.base import Publisher, PublishError, is_http_url
from app.services.social.graph import graph_call, make_client


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
        return await graph_call(   # reel
            client, "POST", f"{page}/videos", token=token,
            data={"file_url": post.media_urls[0], "description": caption},
        )

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
