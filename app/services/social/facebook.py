"""النشر على صفحة فيسبوك (نص، صورة، فيديو) برمز الصفحة."""
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

    def validate(self, post) -> Optional[PublishError]:
        if not settings.META_ACCESS_TOKEN:
            return PublishError(ReportKind.TOKEN, "config_missing:META_ACCESS_TOKEN")
        if not settings.META_PAGE_ID:
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
        if not self._page_token:
            data = await graph_call(
                client, "GET", settings.META_PAGE_ID,
                token=settings.META_ACCESS_TOKEN, params={"fields": "access_token"},
            )
            if not data.get("access_token"):
                raise PublishError(ReportKind.TOKEN, "page_access_token_unavailable")
            self._page_token = data["access_token"]
        return self._page_token

    async def _publish(self, post) -> str:
        caption = post.description or ""
        timeout = self.VIDEO_TIMEOUT if post.media_type == "reel" else 60.0
        async with make_client(timeout, self._transport) as client:
            token = await self._get_page_token(client)
            page = settings.META_PAGE_ID
            if post.media_type == "text":
                body = await graph_call(client, "POST", f"{page}/feed", token=token, data={"message": caption})
            elif post.media_type == "image":
                body = await graph_call(
                    client, "POST", f"{page}/photos", token=token,
                    data={"url": post.media_urls[0], "caption": caption},
                )
            else:   # reel
                body = await graph_call(
                    client, "POST", f"{page}/videos", token=token,
                    data={"file_url": post.media_urls[0], "description": caption},
                )
        remote_id = body.get("post_id") or body.get("id")
        if not remote_id:
            raise PublishError(ReportKind.UNKNOWN, f"no_id_in_response:{body}"[:300])
        return str(remote_id)
