"""محوّلات النشر: تصنيف الأخطاء، التحقق المسبق (بلا شبكة)، وتدفق الطلبات بنقل وهمي (httpx.MockTransport)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx

from app.core.config import settings
from app.models import ReportKind
from app.services.social.base import classify_graph_error, url_extension
from app.services.social.facebook import FacebookPublisher
from app.services.social.instagram import InstagramPublisher


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

    def test_webp_rejected(self):
        self.assertTrue(self.check(post(urls=["https://hadjretbladi.com/B001/1.webp"])).startswith("instagram_image_must_be_jpeg"))

    def test_caption_over_2200_rejected(self):
        self.assertTrue(self.check(post(caption="ا" * 2201)).startswith("caption_too_long"))
        self.assertIsNone(self.check(post(caption="ا" * 2200)))

    def test_missing_ig_id_is_clear(self):
        with mock.patch.object(settings, "META_IG_USER_ID", ""):
            self.assertEqual(self.check(post()), "config_missing:META_IG_USER_ID")

    def test_gallery_unsupported(self):
        self.assertEqual(self.check(post("gallery", ["https://a/1.jpg", "https://a/2.jpg"])), "gallery_not_supported_in_phase1")


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
    def test_reel_waits_for_finished_before_publish(self):
        calls, statuses = [], iter(["IN_PROGRESS", "FINISHED"])

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            calls.append(path.rsplit("/", 1)[-1])
            if path.endswith("/IG1/media"):
                return httpx.Response(200, json={"id": "C1"})
            if path.endswith("/C1"):
                return httpx.Response(200, json={"status_code": next(statuses)})
            return httpx.Response(200, json={"id": "M1"})

        async def no_sleep(_):
            return None

        pub = InstagramPublisher(transport=httpx.MockTransport(handler), sleep=no_sleep)
        result = run(pub.publish(post("reel", ["https://hadjretbladi.com/B001/2.mp4"])))
        self.assertTrue(result.ok)
        self.assertEqual(result.remote_id, "M1")
        self.assertEqual(calls, ["media", "C1", "C1", "media_publish"])

    def test_container_error_fails_without_publishing(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path.rsplit("/", 1)[-1])
            if request.url.path.endswith("/IG1/media"):
                return httpx.Response(200, json={"id": "C1"})
            return httpx.Response(200, json={"status_code": "ERROR", "status": "bad video"})

        pub = InstagramPublisher(transport=httpx.MockTransport(handler))
        result = run(pub.publish(post("reel", ["https://hadjretbladi.com/B001/2.mp4"])))
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, ReportKind.MEDIA)
        self.assertNotIn("media_publish", calls)

    def test_image_publishes_in_two_steps(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"id": "X"})

        result = run(InstagramPublisher(transport=httpx.MockTransport(handler)).publish(post()))
        self.assertTrue(result.ok)
        self.assertEqual(calls, ["media", "media_publish"])


if __name__ == "__main__":
    unittest.main()
