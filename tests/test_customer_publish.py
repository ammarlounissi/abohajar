"""نشر فيديو الزبون على صفحته المربوطة: الناشر الموجود، برمز الصفحة المحفوظ عند الربط."""
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
from app.routers import customer_workspace
from app.services import post_publisher
from app.services.social.base import Publisher, PublishError
from app.services.social.facebook import FacebookPublisher
from tests.test_customer_workspace import BASE, WorkspaceTestBase


class RecordingPublisher(Publisher):
    """محوّل فيسبوك وهمي: يسجّل القناة والرمز اللذين نُشر بهما، وينجح أو يفشل حسب fail."""
    platform = "facebook"

    def __init__(self):
        self.fail = None
        self.calls = []

    def validate(self, post):
        return None

    async def _publish(self, post):
        self.calls.append((self.channel_id, self.channel_token, post.media_urls[0], post.description))
        if self.fail:
            raise self.fail
        return f"{self.channel_id}_{len(self.calls)}"


class CustomerPublishTests(WorkspaceTestBase):
    def setUp(self):
        super().setUp()
        self.publisher = RecordingPublisher()
        self.publisher.calls = self.publisher.calls     # مشترك مع نسخ for_channel
        self.run_background = True

        async def publish_now(post_id):
            """بديل النشر بعد الرد: الناشر الحقيقي بمحوّل وهمي وعلى قاعدة الاختبار."""
            if not self.run_background:
                return
            with self.Session() as db:
                post = db.get(models.Post, post_id)
                await post_publisher.publish_post(db, post, {"facebook": self.publisher}, utc_now())

        patcher = mock.patch.object(
            customer_workspace.post_publisher, "publish_claimed_in_background", side_effect=publish_now,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_video(self, user_id=1, name="v.mp4"):
        """فيديو في مكتبة صفحة المستخدم (كما يحفظه عامل التوليد). يرجع معرّفه."""
        with self.Session() as db:
            row = db.query(models.CustomerWorkspace).filter_by(user_id=user_id).first()
            item = models.Media(
                factory_id=row.factory_id, media_type="video",
                url=f"http://testserver/api/media/factories/{row.factory_id}/{name}",
            )
            db.add(item)
            db.commit()
            return item.id

    def publish(self, client, media_id, caption="فيديو جديد"):
        return client.post(f"{BASE}/videos/{media_id}/publish", json={"caption": caption})

    # ───── الوصول ─────

    def test_requires_login_and_a_linked_page(self):
        self.assertEqual(self.client().get(f"{BASE}/videos/posts").status_code, 401)
        self.assertEqual(self.publish(self.client(), 1).status_code, 401)
        self.assertEqual(self.client(1).get(f"{BASE}/videos/posts").status_code, 409)
        self.assertEqual(self.publish(self.client(1), 1).status_code, 409)

    # ───── النشر ─────

    def test_publishes_the_video_on_the_linked_page_with_its_own_token(self):
        self.link(1, page_id="111")
        client = self.client(1)
        media_id = self.add_video()
        res = self.publish(client, media_id, "  منتجاتنا الجديدة  ")
        self.assertEqual(res.status_code, 202, res.text)
        self.assertEqual((res.json()["status"], res.json()["media_id"]), ("publishing", media_id))

        self.assertEqual(len(self.publisher.calls), 1)
        page, token, url, caption = self.publisher.calls[0]
        self.assertEqual((page, token, caption), ("111", "tok", "منتجاتنا الجديدة"))
        self.assertTrue(url.endswith("/v.mp4"))

        posts = client.get(f"{BASE}/videos/posts").json()
        self.assertEqual(len(posts), 1)
        self.assertEqual((posts[0]["status"], posts[0]["media_id"], posts[0]["error"]), ("published", media_id, None))
        self.assertEqual(posts[0]["facebook_url"], "https://www.facebook.com/111/videos/111_1")
        self.assertTrue(posts[0]["published_at"].endswith(("Z", "+00:00")))     # UTC صريح ليعرضه المتصفح بتوقيت الجزائر
        self.assertNotIn("factory_id", posts[0])
        with self.Session() as db:
            post = db.query(models.Post).one()
            self.assertEqual((post.media_type, post.status), ("reel", PostStatus.PUBLISHED))
            self.assertEqual(post.factory_id, self.factory_of(1))
            self.assertEqual([d.channel.page_id for d in post.deliveries], ["111"])

    def test_new_post_is_claimed_so_the_scheduled_publisher_does_not_take_it(self):
        self.link(1)
        self.run_background = False
        self.assertEqual(self.publish(self.client(1), self.add_video()).status_code, 202)
        with self.Session() as db:
            self.assertEqual(db.query(models.Post).one().status, PostStatus.PUBLISHING)
            self.assertEqual(post_publisher.due_post_ids(db, utc_now()), [])

    def test_a_video_is_published_once(self):
        self.link(1)
        client = self.client(1)
        media_id = self.add_video()
        self.publish(client, media_id)
        res = self.publish(client, media_id)
        self.assertEqual(res.status_code, 422)
        self.assertIn("من قبل", res.json()["detail"])
        self.assertEqual(len(self.publisher.calls), 1)

    def test_cannot_publish_twice_while_publishing(self):
        self.link(1)
        self.run_background = False
        client = self.client(1)
        media_id = self.add_video()
        self.publish(client, media_id)
        res = self.publish(client, media_id)
        self.assertEqual(res.status_code, 422)
        self.assertIn("قيد النشر", res.json()["detail"])
        self.assertEqual(client.get(f"{BASE}/videos/posts").json()[0]["status"], "publishing")

    def test_failed_publish_shows_a_friendly_error_and_can_be_retried(self):
        self.link(1)
        client = self.client(1)
        media_id = self.add_video()
        self.publisher.fail = PublishError(ReportKind.TOKEN, "(#190) Error validating access token")
        self.publish(client, media_id, "أول")
        failed = client.get(f"{BASE}/videos/posts").json()[0]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("أعد ربطها", failed["error"])
        self.assertIn("#190", failed["error_detail"])

        self.publisher.fail = None
        res = self.publish(client, media_id, "ثانٍ")
        self.assertEqual(res.status_code, 202, res.text)
        self.assertEqual(res.json()["id"], failed["id"])       # المنشور نفسه، لا منشور جديد
        posts = client.get(f"{BASE}/videos/posts").json()
        self.assertEqual([(p["status"], p["caption"]) for p in posts], [("published", "ثانٍ")])
        with self.Session() as db:
            self.assertEqual(db.query(models.Post).count(), 1)

    def test_retry_after_switching_pages_goes_to_the_new_page(self):
        self.link(1, page_id="111")
        client = self.client(1)
        media_id = self.add_video()
        self.publisher.fail = PublishError(ReportKind.NETWORK, "timeout")
        self.publish(client, media_id)
        # تغيير الصفحة يفتح مساحة أخرى؛ والعودة إلى الأولى تعيد مكتبتها ومنشورها الفاشل
        self.link(1, page_id="222", name="ثانية")
        self.assertEqual(client.get(f"{BASE}/videos/posts").json(), [])
        self.link(1, page_id="111")
        self.publisher.fail = None
        self.assertEqual(self.publish(client, media_id).status_code, 202)
        self.assertEqual([call[0] for call in self.publisher.calls], ["111", "111"])

    def test_generic_error_message(self):
        self.link(1)
        client = self.client(1)
        self.publisher.fail = PublishError(ReportKind.MEDIA, "(#351) Error with video file")
        self.publish(client, self.add_video())
        post = client.get(f"{BASE}/videos/posts").json()[0]
        self.assertEqual(post["error"], "تعذّر النشر على Facebook. أعد المحاولة بعد قليل.")

    def test_stuck_publishing_is_reported_as_unknown(self):
        self.link(1)
        self.run_background = False
        client = self.client(1)
        self.publish(client, self.add_video())
        with self.Session() as db:
            post = db.query(models.Post).one()
            post.claimed_at = utc_now() - timedelta(minutes=settings.PUBLISH_STALE_MINUTES + 1)
            db.commit()
        self.assertEqual(client.get(f"{BASE}/videos/posts").json()[0]["status"], "unknown")

    # ───── مكتبة الفيديو بحالاتها (GET /videos/library) ─────

    def library(self, client):
        res = client.get(f"{BASE}/videos/library")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_library_requires_login_and_a_linked_page(self):
        self.assertEqual(self.client().get(f"{BASE}/videos/library").status_code, 401)
        self.assertEqual(self.client(1).get(f"{BASE}/videos/library").status_code, 409)

    def test_library_lists_each_video_with_its_state_and_counts(self):
        self.link(1, page_id="111")
        client = self.client(1)
        ready = self.add_video(name="ready.mp4")
        published = self.add_video(name="published.mp4")
        failed = self.add_video(name="failed.mp4")
        self.publish(client, published, "منشور")
        self.publisher.fail = PublishError(ReportKind.NETWORK, "timeout")
        self.publish(client, failed, "فاشل")
        self.run_background = False
        self.publisher.fail = None
        publishing = self.add_video(name="publishing.mp4")
        self.publish(client, publishing)
        stuck = self.add_video(name="stuck.mp4")
        self.publish(client, stuck)
        with self.Session() as db:
            post = db.query(models.Post).filter(models.Post.media_urls.contains("stuck.mp4")).one()
            post.claimed_at = utc_now() - timedelta(minutes=settings.PUBLISH_STALE_MINUTES + 1)
            # منشور مجدول (النشر الجماعي) لفيديو في المكتبة ينتظر موعده
            scheduled_url = db.get(models.Media, ready).url.replace("ready.mp4", "scheduled.mp4")
            row = db.query(models.CustomerWorkspace).filter_by(user_id=1).first()
            db.add(models.Media(factory_id=row.factory_id, media_type="video", url=scheduled_url))
            db.add(models.Post(
                factory_id=row.factory_id, media_type="reel", media_urls=[scheduled_url], description="غداً",
                status=PostStatus.APPROVED, scheduled_at=utc_now() + timedelta(days=1),
            ))
            db.commit()

        body = self.library(client)
        states = {video["url"].rsplit("/", 1)[-1]: video["status"] for video in body["videos"]}
        self.assertEqual(states, {
            "scheduled.mp4": "scheduled", "stuck.mp4": "unknown", "publishing.mp4": "publishing",
            "failed.mp4": "failed", "published.mp4": "published", "ready.mp4": "ready",
        })
        self.assertEqual(list(states)[0], "scheduled.mp4")                 # الأحدث أولاً
        self.assertEqual(body["counts"], {
            "all": 6, "ready": 1, "scheduled": 1, "publishing": 1, "published": 1, "failed": 1, "unknown": 1,
        })
        self.assertEqual(body["max_videos"], 20)
        self.assertIsNone(body["generating"])
        by_id = {video["id"]: video for video in body["videos"]}
        self.assertIsNone(by_id[ready]["post"])
        self.assertEqual(by_id[published]["post"]["facebook_url"], "https://www.facebook.com/111/videos/111_1")
        self.assertEqual(by_id[failed]["post"]["caption"], "فاشل")
        self.assertIsNotNone(by_id[failed]["post"]["error"])
        self.assertIsNone(by_id[ready]["template"])                          # بلا مهمة توليد معروفة
        self.assertNotIn("factory_id", body["videos"][0])

    def test_library_follows_a_retry_to_its_new_state(self):
        self.link(1)
        client = self.client(1)
        media_id = self.add_video()
        self.publisher.fail = PublishError(ReportKind.NETWORK, "timeout")
        self.publish(client, media_id)
        self.assertEqual(self.library(client)["videos"][0]["status"], "failed")
        self.publisher.fail = None
        self.publish(client, media_id)
        body = self.library(client)
        self.assertEqual(body["videos"][0]["status"], "published")
        self.assertEqual((body["counts"]["failed"], body["counts"]["published"]), (0, 1))

    def test_library_is_scoped_to_the_users_page(self):
        self.link(1, page_id="111")
        self.link(2, page_id="222")
        self.add_video(user_id=1)
        self.assertEqual(self.library(self.client(2))["counts"]["all"], 0)
        self.assertEqual(self.library(self.client(1))["counts"]["all"], 1)

    # ───── الرفض ─────

    def test_rejects_what_cannot_be_published(self):
        self.link(1)
        client = self.client(1)
        self.assertEqual(self.publish(client, 999).status_code, 404)
        self.upload(client)
        image_id = client.get(f"{BASE}/media").json()[0]["id"]
        self.assertEqual(self.publish(client, image_id).status_code, 404)        # صورة لا فيديو
        self.assertEqual(self.publish(client, self.add_video(), "x" * 2201).status_code, 422)
        with self.Session() as db:   # صفحة بلا رمز (فُكّت ثم بقي صفها)
            db.query(models.SocialChannel).update({"page_access_token": None})
            db.commit()
        res = self.publish(client, self.add_video(name="w.mp4"))
        self.assertEqual(res.status_code, 422)
        self.assertIn("إعادة ربط", res.json()["detail"])
        self.assertEqual(self.publisher.calls, [])

    def test_users_cannot_publish_each_others_videos(self):
        self.link(1, page_id="111")
        self.link(2, page_id="222")
        mine = self.add_video(1)
        self.assertEqual(self.publish(self.client(2), mine).status_code, 404)
        self.publish(self.client(1), mine)
        self.assertEqual(self.client(2).get(f"{BASE}/videos/posts").json(), [])

    def test_video_being_published_cannot_be_deleted(self):
        self.link(1)
        self.run_background = False
        client = self.client(1)
        media_id = self.add_video()
        self.publish(client, media_id)
        res = client.delete(f"{BASE}/media/{media_id}")
        self.assertEqual(res.status_code, 422)
        self.assertIn("قيد النشر", res.json()["detail"])

    def test_published_video_can_be_deleted_and_its_post_stays(self):
        self.link(1)
        client = self.client(1)
        media_id = self.add_video()
        self.publish(client, media_id)
        self.assertEqual(client.delete(f"{BASE}/media/{media_id}").status_code, 204)
        posts = client.get(f"{BASE}/videos/posts").json()
        self.assertEqual((posts[0]["status"], posts[0]["media_id"]), ("published", None))


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
