"""النشر على إنستغرام (صورة JPEG أو ريلز MP4) برمز مستخدم النظام مباشرة."""
import asyncio
from typing import Optional

from app.core.config import settings
from app.models import ReportKind
from app.services.social.base import (
    INSTAGRAM_CAPTION_LIMIT, Publisher, PublishError, is_http_url, url_extension,
)
from app.services.social.graph import graph_call, make_client


class InstagramPublisher(Publisher):
    platform = "instagram"
    POLL_INTERVAL = 5.0       # ثوانٍ بين فحوص حالة الريلز
    POLL_TIMEOUT = 300.0      # أقصى انتظار لمعالجة الفيديو عند ميتا

    def __init__(self, transport=None, sleep=asyncio.sleep):
        self._transport = transport
        self._sleep = sleep

    @property
    def _ig_id(self) -> str:
        return self.channel_id or settings.META_IG_USER_ID

    def validate(self, post) -> Optional[PublishError]:
        if not settings.META_ACCESS_TOKEN:
            return PublishError(ReportKind.TOKEN, "config_missing:META_ACCESS_TOKEN")
        if not self._ig_id:
            return PublishError(ReportKind.UNKNOWN, "config_missing:META_IG_USER_ID")
        media_type, urls = post.media_type, list(post.media_urls or [])
        if media_type == "gallery":
            return PublishError(ReportKind.MEDIA, "gallery_not_supported_in_phase1")
        if media_type == "text" or not urls:
            return PublishError(ReportKind.MEDIA, "instagram_requires_media")
        if len(post.description or "") > INSTAGRAM_CAPTION_LIMIT:
            return PublishError(ReportKind.MEDIA, f"caption_too_long:{len(post.description)}>{INSTAGRAM_CAPTION_LIMIT}")
        url = urls[0]
        if not is_http_url(url):
            return PublishError(ReportKind.MEDIA, "media_url_not_http")
        ext = url_extension(url)
        if media_type == "image" and ext not in (".jpg", ".jpeg"):
            return PublishError(ReportKind.MEDIA, f"instagram_image_must_be_jpeg:{ext or 'none'}")
        if media_type == "reel" and ext != ".mp4":
            return PublishError(ReportKind.MEDIA, f"instagram_reel_must_be_mp4:{ext or 'none'}")
        return None

    async def _wait_until_finished(self, client, creation_id: str) -> None:
        waited = 0.0
        while True:
            data = await graph_call(
                client, "GET", creation_id, token=settings.META_ACCESS_TOKEN,
                params={"fields": "status_code,status"},
            )
            code = data.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(ReportKind.MEDIA, f"instagram_container_{code.lower()}:{data.get('status', '')}"[:300])
            if waited >= self.POLL_TIMEOUT:
                raise PublishError(ReportKind.MEDIA, f"instagram_container_timeout:{code}")
            await self._sleep(self.POLL_INTERVAL)
            waited += self.POLL_INTERVAL

    async def _publish(self, post) -> str:
        token, ig = settings.META_ACCESS_TOKEN, self._ig_id
        is_reel = post.media_type == "reel"
        fields = {"caption": post.description or ""}
        if is_reel:
            fields.update(media_type="REELS", video_url=post.media_urls[0])
        else:
            fields["image_url"] = post.media_urls[0]
        async with make_client(60.0, self._transport) as client:
            container = await graph_call(client, "POST", f"{ig}/media", token=token, data=fields)
            creation_id = container.get("id")
            if not creation_id:
                raise PublishError(ReportKind.UNKNOWN, f"no_creation_id:{container}"[:300])
            if is_reel:
                await self._wait_until_finished(client, creation_id)
            published = await graph_call(
                client, "POST", f"{ig}/media_publish", token=token, data={"creation_id": creation_id},
            )
        if not published.get("id"):
            raise PublishError(ReportKind.UNKNOWN, f"no_id_in_response:{published}"[:300])
        return str(published["id"])
