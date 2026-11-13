"""أرشيف الفيديوهات القديمة (من التوليد والنشر اليدوي قبل توحيد المنشورات)، ومحوّل فيسبوك برمز الصفحة.

النشر اليدوي الفوري أُلغي: كل نشر صار عبر المنشورات بقيود الجدولة (tests/test_post_drafts.py). ما نُشر أو وُلّد
يدوياً من قبل يبقى أرشيفاً للعرض والحذف."""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import httpx

from app import models
from app.core.config import settings
from app.core.time import utc_now
from app.models import PostStatus, ReportKind
from app.services.social.facebook import FacebookPublisher
from tests.test_customer_workspace import BASE, WorkspaceTestBase


class VideoArchiveTests(WorkspaceTestBase):
    def add_video(self, user_id=1, name="v.mp4", page_id="111"):
        """فيديو قديم في مكتبة صفحة المستخدم. يرجع (معرّفه، رابطه)."""
        with self.Session() as db:
            factory_id = self.factory_of(user_id, page_id)
            item = models.Media(
                factory_id=factory_id, media_type="video",
                url=f"http://testserver/api/media/factories/{factory_id}/{name}",
            )
            db.add(item)
            db.commit()
            return item.id, item.url

    def add_post(self, url, status, user_id=1, caption="نص قديم", error=None, remote_id=None, **fields):
        """منشور يدوي قديم للفيديو (كما كان يُنشئه النشر الفوري)."""
        with self.Session() as db:
            row = db.query(models.CustomerWorkspace).filter_by(user_id=user_id).first()
            channel = db.query(models.SocialChannel).filter_by(page_id=row.page_id).first()
            post = models.Post(
                factory_id=row.factory_id, media_type="reel", media_urls=[url], description=caption,
                status=status, error=error, **fields,
            )
            post.deliveries = [models.PostDelivery(
                channel_id=channel.id, status="published" if remote_id else "failed" if error else "pending",
                remote_id=remote_id, error=error, attempts=1,
            )]
            db.add(post)
            db.commit()
            return post.id

    def archive(self, client):
        res = client.get(f"{BASE}/videos/archive")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_requires_login_and_a_linked_page(self):
        self.assertEqual(self.client().get(f"{BASE}/videos/archive").status_code, 401)
        self.assertEqual(self.client(1).get(f"{BASE}/videos/archive").status_code, 409)

    def test_lists_old_videos_with_their_last_manual_state(self):
        self.link(1, page_id="111")
        client = self.client(1)
        ready, _ = self.add_video(name="ready.mp4")
        published, published_url = self.add_video(name="published.mp4")
        failed, failed_url = self.add_video(name="failed.mp4")
        stuck, stuck_url = self.add_video(name="stuck.mp4")
        self.add_post(published_url, PostStatus.PUBLISHED, remote_id="111_9", published_at=utc_now())
        self.add_post(failed_url, PostStatus.FAILED, caption="فاشل", error="(#190) Error validating access token")
        self.add_post(stuck_url, PostStatus.PUBLISHING,
                      claimed_at=utc_now() - timedelta(minutes=settings.PUBLISH_STALE_MINUTES + 1))

        videos = self.archive(client)
        self.assertEqual([video["id"] for video in videos], [stuck, failed, published, ready])   # الأحدث أولاً
        by_id = {video["id"]: video for video in videos}
        self.assertEqual(
            {media_id: video["status"] for media_id, video in by_id.items()},
            {ready: "ready", published: "published", failed: "failed", stuck: "unknown"},
        )
        self.assertIsNone(by_id[ready]["post"])
        self.assertEqual(by_id[published]["post"]["facebook_url"], "https://www.facebook.com/111/videos/111_9")
        self.assertTrue(by_id[published]["post"]["published_at"].endswith(("Z", "+00:00")))
        self.assertEqual(by_id[failed]["post"]["caption"], "فاشل")
        self.assertIn("أعد ربطها", by_id[failed]["post"]["error"])
        self.assertIn("#190", by_id[failed]["post"]["error_detail"])
        self.assertIsNone(by_id[ready]["template"])                        # بلا مهمة توليد معروفة
        self.assertNotIn("factory_id", videos[0])

    def test_videos_of_posts_are_not_in_the_archive(self):
        """فيديو وُلّد لمنشور (مسودة) يُعرض مع منشوره في قائمة المنشورات."""
        self.link(1)
        client = self.client(1)
        old, _ = self.add_video(name="old.mp4")
        _, draft_url = self.add_video(name="draft.mp4")
        with self.Session() as db:
            factory_id = self.factory_of(1)
            job = models.FactoryVideoJob(factory_id=factory_id, source="library", template="quick_7", duration=7,
                                         status="succeeded", video_url=draft_url)
            db.add(job)
            db.flush()
            db.add(models.PostDraft(factory_id=factory_id, user_id=1, status="scheduled", template="quick_7",
                                    image_ids=[], caption="منشور", scheduled_at=utc_now() + timedelta(days=1),
                                    video_job_id=job.id))
            db.commit()
        self.assertEqual([video["id"] for video in self.archive(client)], [old])

    def test_archive_is_scoped_to_the_users_page(self):
        self.link(1, page_id="111")
        self.link(2, page_id="222")
        self.add_video(user_id=1)
        self.assertEqual(self.archive(self.client(2)), [])
        self.assertEqual(len(self.archive(self.client(1))), 1)

    def test_old_videos_cannot_be_published_any_more(self):
        self.link(1)
        client = self.client(1)
        media_id, _ = self.add_video()
        self.assertIn(client.post(f"{BASE}/videos/{media_id}/publish", json={"caption": "x"}).status_code, (404, 405))
        with self.Session() as db:
            self.assertEqual(db.query(models.Post).count(), 0)

    def test_video_being_published_cannot_be_deleted(self):
        self.link(1)
        client = self.client(1)
        media_id, url = self.add_video()
        self.add_post(url, PostStatus.PUBLISHING, claimed_at=utc_now())
        res = client.delete(f"{BASE}/media/{media_id}")
        self.assertEqual(res.status_code, 422)
        self.assertIn("قيد النشر", res.json()["detail"])

    def test_published_video_can_be_deleted_and_its_post_stays(self):
        self.link(1)
        client = self.client(1)
        media_id, url = self.add_video()
        self.add_post(url, PostStatus.PUBLISHED, remote_id="111_1", published_at=utc_now())
        self.assertEqual(client.delete(f"{BASE}/media/{media_id}").status_code, 204)
        self.assertEqual(self.archive(client), [])
        with self.Session() as db:
            self.assertEqual(db.query(models.Post).one().status, PostStatus.PUBLISHED)


def _post(media_type="reel", urls=("https://api.example.test/v.mp4",), caption="وصف"):
    return SimpleNamespace(media_type=media_type, media_urls=list(urls), description=caption)


class FacebookChannelTokenTests(unittest.TestCase):
    """محوّل فيسبوك برمز الصفحة المحفوظ معها (صفحة زبون) بدل رمز النظام."""

    def setUp(self):
        for name, value in (("META_ACCESS_TOKEN", "SYSTEM"), ("GRAPH_API_VERSION", "v19.0")):
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def publish(self, handler, token="CUSTOMER"):
        publisher = FacebookPublisher(transport=httpx.MockTransport(handler)).for_channel("PAGE9", token)
        return asyncio.run(publisher.publish(_post()))

    def test_channel_token_is_used_directly(self):
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path.rsplit("/", 2)[-2:], request.headers["authorization"]))
            return httpx.Response(200, json={"id": "777"})

        result = self.publish(handler)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.remote_id, "777")
        self.assertEqual(seen, [("POST", ["PAGE9", "videos"], "Bearer CUSTOMER")])   # بلا طلب رمز من النظام

    def test_works_without_a_system_token(self):
        with mock.patch.object(settings, "META_ACCESS_TOKEN", ""):
            self.assertTrue(self.publish(lambda r: httpx.Response(200, json={"id": "1"})).ok)
            no_token = FacebookPublisher().for_channel("PAGE9")
            self.assertEqual(no_token.validate(_post()).kind, ReportKind.TOKEN)

    def test_rejected_channel_token_falls_back_to_the_system_token(self):
        seen = []

        def handler(request):
            auth = request.headers["authorization"]
            seen.append((request.method, auth))
            if auth == "Bearer CUSTOMER":
                return httpx.Response(400, json={"error": {"message": "expired", "code": 190}})
            if request.method == "GET":
                return httpx.Response(200, json={"access_token": "PAGEFROMSYSTEM"})
            return httpx.Response(200, json={"id": "888"})

        result = self.publish(handler)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(seen, [("POST", "Bearer CUSTOMER"), ("GET", "Bearer SYSTEM"), ("POST", "Bearer PAGEFROMSYSTEM")])

    def test_when_both_tokens_fail_the_channel_token_error_is_reported(self):
        def handler(request):
            if request.headers["authorization"] == "Bearer CUSTOMER":
                return httpx.Response(400, json={"error": {"message": "expired", "code": 190}})
            return httpx.Response(400, json={"error": {"message": "no access to page", "code": 100}})

        result = self.publish(handler)
        self.assertFalse(result.ok)
        self.assertEqual(result.kind, ReportKind.TOKEN)
        self.assertIn("(#190) expired", result.error)

    def test_other_errors_do_not_retry_with_the_system_token(self):
        calls = []

        def handler(request):
            calls.append(request.headers["authorization"])
            return httpx.Response(400, json={"error": {"message": "bad video", "code": 351}})

        result = self.publish(handler)
        self.assertEqual((result.ok, result.kind), (False, ReportKind.MEDIA))
        self.assertEqual(calls, ["Bearer CUSTOMER"])


if __name__ == "__main__":
    unittest.main()
