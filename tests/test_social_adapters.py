"""محوّلات النشر: تصنيف الأخطاء، التحقق المسبق (بلا شبكة)، وتدفق الطلبات بنقل وهمي (httpx.MockTransport)."""
import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import settings
from app.models import ReportKind
from app.services.social.base import classify_graph_error, url_extension
from app.services.social.facebook import FacebookPublisher
from app.services.social.instagram import InstagramPublisher
from app.services import instagram_media
from app.services.instagram_media import _fit_image, derivative_path
from app.routers.public_media import router as public_media_router


def post(media_type="image", urls=("https://hadjretbladi.com/B001/1.jpg",), caption="وصف"):
    return SimpleNamespace(media_type=media_type, media_urls=list(urls), description=caption)


def run(coro):
    return asyncio.run(coro)


class ConfiguredCase(unittest.TestCase):
    def setUp(self):
        for name, value in (("META_ACCESS_TOKEN", "USERTOKEN"), ("META_PAGE_ID", "PAGE1"),
                            ("META_IG_USER_ID", "IG1"), ("GRAPH_API_VERSION", "v19.0")):
            p = mock.patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)


class ClassifyTests(unittest.TestCase):
    def test_token_codes(self):
        for code in (190, 463):
            self.assertEqual(classify_graph_error(400, {"code": code}), ReportKind.TOKEN)

    def test_limit_and_media_and_network(self):
        self.assertEqual(classify_graph_error(400, {"code": 9, "error_subcode": 2207042}), ReportKind.LIMIT)
        self.assertEqual(classify_graph_error(400, {"code": 4}), ReportKind.LIMIT)
        self.assertEqual(classify_graph_error(400, {"code": 9004, "error_subcode": 2207052}), ReportKind.MEDIA)
        self.assertEqual(classify_graph_error(502, None), ReportKind.NETWORK)
        self.assertEqual(classify_graph_error(400, {"code": 12345}), ReportKind.UNKNOWN)

    def test_url_extension_ignores_query(self):
        self.assertEqual(url_extension("https://x.com/B001/1.JPG?v=2"), ".jpg")
        self.assertEqual(url_extension("https://x.com/B001/"), "")


class InstagramValidateTests(ConfiguredCase):
    def check(self, p):
        err = InstagramPublisher().validate(p)
        return err.message if err else None

    def test_valid_image_and_reel(self):
        self.assertIsNone(self.check(post()))
        self.assertIsNone(self.check(post("reel", ["https://hadjretbladi.com/B001/2.mp4"])))

    def test_text_post_rejected(self):
        self.assertEqual(self.check(post("text", [])), "instagram_requires_media")

    def test_webp_image_url_is_accepted_for_conversion(self):
        self.assertIsNone(self.check(post(urls=["https://hadjretbladi.com/B001/1.webp"])))

    def test_caption_over_2200_rejected(self):
        self.assertTrue(self.check(post(caption="ا" * 2201)).startswith("caption_too_long"))
        self.assertIsNone(self.check(post(caption="ا" * 2200)))

    def test_missing_ig_id_is_clear(self):
        with mock.patch.object(settings, "META_IG_USER_ID", ""):
            self.assertEqual(self.check(post()), "config_missing:META_IG_USER_ID")

    def test_gallery_unsupported(self):
        self.assertEqual(self.check(post("gallery", ["https://a/1.jpg", "https://a/2.jpg"])), "gallery_not_supported_in_phase1")


class InstagramImageFitTests(unittest.TestCase):
    def test_crop_and_resize_to_supported_aspect_ratio(self):
        source = Image.new("RGB", (6000, 1000), "white")
        encoded = io.BytesIO()
        source.save(encoded, format="PNG")

        fitted = Image.open(io.BytesIO(_fit_image(encoded.getvalue())))

        self.assertEqual(fitted.format, "JPEG")
        self.assertEqual(fitted.width, 1440)
        self.assertGreaterEqual(fitted.width / fitted.height, 4 / 5)
        self.assertLessEqual(fitted.width / fitted.height, 1.91)

    def test_portrait_image_is_cropped_to_four_by_five(self):
        source = Image.new("RGB", (400, 1000), "white")
        encoded = io.BytesIO()
        source.save(encoded, format="JPEG")

        fitted = Image.open(io.BytesIO(_fit_image(encoded.getvalue())))

        self.assertEqual(fitted.width / fitted.height, 4 / 5)
        self.assertGreaterEqual(fitted.width, 320)


class InstagramVideoFitTests(ConfiguredCase):
    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)

    def test_transcode_crops_and_scales_to_vertical_reel_dimensions(self):
        command = []
        with mock.patch.object(instagram_media.ffmpeg_tools, "find_tool", return_value="/usr/bin/ffmpeg"), \
                mock.patch.object(
                    instagram_media, "_run_media_command",
                    side_effect=lambda args, **kwargs: command.extend(args),
                ):
            instagram_media._transcode_reel(Path("source.mp4"), Path("output.mp4"))

        filter_arg = command[command.index("-vf") + 1]
        self.assertEqual(
            filter_arg,
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
        )
        self.assertIn("libx264", command)
        self.assertEqual(command[-1], "output.mp4")

    def test_downloads_transcodes_caches_and_serves_mp4_derivative(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/B001/2.mp4")
            return httpx.Response(200, content=b"source video")

        def transcode(_source: Path, output: Path) -> None:
            output.write_bytes(b"normalized 1080x1920 mp4")

        with mock.patch.object(settings, "MEDIA_ROOT", Path(self.tempdir.name)), \
                mock.patch.object(settings, "BASE_URL", "https://api.hadjretbladi.com"), \
                mock.patch.object(instagram_media, "_video_dimensions", return_value=(1920, 1080)) as probe, \
                mock.patch.object(instagram_media, "_transcode_reel", side_effect=transcode) as encode:
            url = run(instagram_media.prepare_instagram_video(
                "https://hadjretbladi.com/B001/2.mp4", httpx.MockTransport(handler),
            ))
            self.assertRegex(
                url,
                r"^https://api\.hadjretbladi\.com/api/media/instagram/[0-9a-f]{64}\.mp4$",
            )
            probe.assert_called_once()
            encode.assert_called_once()

            filename = url.rsplit("/", 1)[1]
            media_path = derivative_path(filename)
            self.assertEqual(media_path.read_bytes(), b"normalized 1080x1920 mp4")
            app = FastAPI()
            app.include_router(public_media_router)
            response = TestClient(app).get(f"/api/media/instagram/{filename}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "video/mp4")

    def test_already_correct_dimensions_keep_the_source_url(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"source video")

        with mock.patch.object(settings, "MEDIA_ROOT", Path(self.tempdir.name)), \
                mock.patch.object(settings, "BASE_URL", "https://api.hadjretbladi.com"), \
                mock.patch.object(
                    instagram_media, "_video_dimensions",
                    return_value=(instagram_media.REEL_WIDTH, instagram_media.REEL_HEIGHT),
                ), \
                mock.patch.object(instagram_media, "_transcode_reel") as encode:
            url = run(instagram_media.prepare_instagram_video(
                "https://hadjretbladi.com/B001/2.mp4", httpx.MockTransport(handler),
            ))

        self.assertEqual(url, "https://hadjretbladi.com/B001/2.mp4")
        encode.assert_not_called()


class FacebookValidateTests(ConfiguredCase):
    def test_text_needs_caption(self):
        self.assertIsNotNone(FacebookPublisher().validate(post("text", [], caption="  ")))
        self.assertIsNone(FacebookPublisher().validate(post("text", [], caption="مرحبا")))

    def test_image_needs_http_url(self):
        self.assertIsNotNone(FacebookPublisher().validate(post(urls=["/local/1.jpg"])))


class FacebookFlowTests(ConfiguredCase):
    def test_photo_uses_page_token_and_returns_post_id(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, request.headers["authorization"]))
            if request.method == "GET":
                return httpx.Response(200, json={"access_token": "PAGETOKEN", "id": "PAGE1"})
            return httpx.Response(200, json={"id": "111", "post_id": "PAGE1_111"})

        result = run(FacebookPublisher(transport=httpx.MockTransport(handler)).publish(post()))
        self.assertTrue(result.ok)
        self.assertEqual(result.remote_id, "PAGE1_111")
        self.assertEqual(seen[0][2], "Bearer USERTOKEN")       # جلب رمز الصفحة برمز مستخدم النظام
        self.assertEqual(seen[1][1].rsplit("/", 1)[-1], "photos")
        self.assertEqual(seen[1][2], "Bearer PAGETOKEN")       # النشر برمز الصفحة

    def test_token_error_is_classified(self):
        handler = lambda r: httpx.Response(400, json={"error": {"message": "expired", "code": 190}})
        result = run(FacebookPublisher(transport=httpx.MockTransport(handler)).publish(post("text", [], "نص")))
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, ReportKind.TOKEN)
        self.assertIn("(#190)", result.error)


class InstagramFlowTests(ConfiguredCase):
    @staticmethod
    def image_bytes(size=(1200, 400)):
        image = Image.new("RGB", size, "white")
        output = io.BytesIO()
        image.save(output, format="JPEG")
        return output.getvalue()

    def test_reel_waits_for_finished_before_publish(self):
        calls, video_urls, statuses = [], [], iter(["IN_PROGRESS", "FINISHED"])

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            calls.append(path.rsplit("/", 1)[-1])
            if path.endswith("/IG1/media"):
                video_urls.extend(parse_qs(request.content.decode())["video_url"])
                return httpx.Response(200, json={"id": "C1"})
            if path.endswith("/C1"):
                return httpx.Response(200, json={"status_code": next(statuses)})
            return httpx.Response(200, json={"id": "M1"})

        async def no_sleep(_):
            return None

        pub = InstagramPublisher(transport=httpx.MockTransport(handler), sleep=no_sleep)
        with mock.patch(
            "app.services.social.instagram.prepare_instagram_video",
            new=mock.AsyncMock(
                return_value="https://api.hadjretbladi.com/api/media/instagram/" + "a" * 64 + ".mp4",
            ),
        ) as prepare_video:
            result = run(pub.publish(post("reel", ["https://hadjretbladi.com/B001/2.mp4"])))
        self.assertTrue(result.ok)
        self.assertEqual(result.remote_id, "M1")
        self.assertEqual(calls, ["media", "C1", "C1", "media_publish"])
        self.assertEqual(
            video_urls,
            ["https://api.hadjretbladi.com/api/media/instagram/" + "a" * 64 + ".mp4"],
        )
        prepare_video.assert_called_once()

    def test_container_error_fails_without_publishing(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path.rsplit("/", 1)[-1])
            if request.url.path.endswith("/IG1/media"):
                return httpx.Response(200, json={"id": "C1"})
            return httpx.Response(200, json={"status_code": "ERROR", "status": "bad video"})

        pub = InstagramPublisher(transport=httpx.MockTransport(handler))
        with mock.patch(
            "app.services.social.instagram.prepare_instagram_video",
            new=mock.AsyncMock(return_value="https://api.example.com/reel.mp4"),
        ):
            result = run(pub.publish(post("reel", ["https://hadjretbladi.com/B001/2.mp4"])))
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, ReportKind.MEDIA)
        self.assertNotIn("media_publish", calls)

    def test_image_publishes_in_two_steps(self):
        calls, image_urls = [], []
        source_image = self.image_bytes()

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "hadjretbladi.com":
                calls.append("source_image")
                return httpx.Response(200, content=source_image, headers={"Content-Type": "image/jpeg"})
            calls.append(request.url.path.rsplit("/", 1)[-1])
            if request.url.path.endswith("/media"):
                image_urls.extend(parse_qs(request.content.decode())["image_url"])
            return httpx.Response(200, json={"id": "X"})

        with mock.patch.object(settings, "MEDIA_ROOT", Path(self.tempdir.name)), \
                mock.patch.object(settings, "BASE_URL", "https://api.hadjretbladi.com"):
            result = run(InstagramPublisher(transport=httpx.MockTransport(handler)).publish(post()))
        self.assertTrue(result.ok)
        self.assertEqual(calls, ["source_image", "media", "media_publish"])
        self.assertRegex(image_urls[0], r"^https://api\.hadjretbladi\.com/api/media/instagram/[0-9a-f]{64}\.jpg$")
        filename = image_urls[0].rsplit("/", 1)[1]
        with mock.patch.object(settings, "MEDIA_ROOT", Path(self.tempdir.name)):
            self.assertEqual(derivative_path(filename), Path(self.tempdir.name) / ".instagram" / filename)
            self.assertIsNone(derivative_path("../" + filename))
            app = FastAPI()
            app.include_router(public_media_router)
            response = TestClient(app).get(f"/api/media/instagram/{filename}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "image/jpeg")
            self.assertIn("immutable", response.headers["cache-control"])
        with Image.open(Path(self.tempdir.name) / ".instagram" / filename) as fitted:
            self.assertEqual(fitted.format, "JPEG")
            self.assertGreaterEqual(fitted.width / fitted.height, 4 / 5)
            self.assertLessEqual(fitted.width / fitted.height, 1.91)

    def setUp(self):
        super().setUp()
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)


if __name__ == "__main__":
    unittest.main()
