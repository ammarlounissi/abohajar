"""رئيسية لوحة الزبون: مظهر صفحته في فيسبوك، وما ينتظره منه، وما أُنجز (GET /overview)."""
from datetime import timedelta
from unittest import mock

import httpx

from app import models
from app.models import PostStatus
from app.services import page_appearance
from tests.test_customer_workspace import BASE
from tests.test_post_drafts import NOW, DraftTestBase

LOOK = {
    "picture_url": "https://scontent.example/p.jpg", "cover_url": "https://scontent.example/c.jpg",
    "cover_offset_y": 30, "followers": 1520, "link": "https://www.facebook.com/111",
}


class CustomerOverviewTests(DraftTestBase):
    def setUp(self):
        super().setUp()
        page_appearance.forget()
        self.addCleanup(page_appearance.forget)
        self.look = dict(LOOK)
        self.fetches = []

        def fake_fetch(page_id, token):
            self.fetches.append((page_id, token))
            return self.look

        patcher = mock.patch.object(page_appearance, "fetch_page_appearance", side_effect=fake_fetch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def overview(self, client):
        res = client.get(f"{BASE}/overview")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_requires_login_and_a_linked_page(self):
        self.assertEqual(self.client().get(f"{BASE}/overview").status_code, 401)
        self.assertEqual(self.client(1).get(f"{BASE}/overview").status_code, 409)

    def test_new_page_shows_its_facebook_look_and_what_is_missing(self):
        self.link(1, name="متجر الإخلاص")
        data = self.overview(self.client(1))
        self.assertEqual(data["page"], {"page_id": "111", "name": "متجر الإخلاص", "is_active": True, **LOOK})
        self.assertEqual(self.fetches, [("111", "tok")])           # بتوكن الصفحة
        self.assertEqual(data["missing_settings"], ["activity", "whatsapp", "hashtags"])
        for key in ("images", "videos", "drafts_to_review", "drafts_writing", "scheduled", "published", "failed_scheduled",
                    "failed_videos", "notification_devices"):
            self.assertEqual(data[key], 0, key)
        self.assertIsNone(data["next_scheduled_at"])
        self.assertFalse(data["video_generating"])

    def test_written_settings_are_no_longer_missing(self):
        client = self.ready_client(images=0)                      # يكتب النشاط
        self.assertEqual(self.overview(client)["missing_settings"], ["whatsapp", "hashtags"])
        client.patch(f"{BASE}/profile", json={"whatsapp": "0550123456", "hashtags": ["خبز"]})
        self.assertEqual(self.overview(client)["missing_settings"], [])

    def test_counts_follow_the_drafts_from_review_to_published(self):
        client = self.ready_client(images=4)
        self.generate(client, 3)
        data = self.overview(client)
        self.assertEqual((data["images"], data["drafts_writing"], data["drafts_to_review"]), (4, 3, 0))

        self.write_captions()
        drafts = self.drafts(client)
        self.assertEqual(self.overview(client)["drafts_to_review"], 3)

        client.post(f"{BASE}/drafts/approve", json={"ids": [d["id"] for d in drafts[:2]]})
        data = self.overview(client)
        self.assertEqual((data["drafts_to_review"], data["scheduled"]), (1, 2))
        self.assertEqual(data["next_scheduled_at"].replace("+00:00", "Z"), drafts[0]["scheduled_at"].replace("+00:00", "Z"))

        self.now = NOW + timedelta(hours=20)                       # يُرندر الأول ويُجدول
        self.render_all()
        with self.Session() as db:
            post = db.query(models.Post).one()
            post.status, post.published_at = PostStatus.PUBLISHED, self.now
            db.commit()
            second = db.get(models.PostDraft, drafts[1]["id"])
            second.status, second.error = "failed", "تعذّر"
            db.commit()
        data = self.overview(client)
        self.assertEqual((data["published"], data["scheduled"], data["failed_scheduled"], data["drafts_to_review"]),
                         (1, 0, 1, 1))
        self.assertEqual(data["failed_videos"], 0)
        self.assertEqual(data["videos"], 1)                        # فيديو المسودة في المكتبة

    def test_facebook_look_is_cached_and_a_failure_shows_an_empty_look(self):
        self.link(1)
        client = self.client(1)
        self.overview(client)
        self.overview(client)
        self.assertEqual(len(self.fetches), 1)

        page_appearance.forget()
        self.look = None
        page = self.overview(client)["page"]
        self.assertEqual((page["picture_url"], page["cover_url"], page["followers"]), (None, None, None))
        self.assertEqual(page["cover_offset_y"], 50)

    def test_each_user_sees_only_their_own_page(self):
        self.ready_client(user_id=1, images=2)
        self.link(2, page_id="222", name="صفحة أخرى")
        data = self.overview(self.client(2))
        self.assertEqual((data["page"]["page_id"], data["images"]), ("222", 0))


class FetchPageAppearanceTests(DraftTestBase):
    def fetch(self, handler):
        return page_appearance.fetch_page_appearance("111", "tok", transport=httpx.MockTransport(handler))

    def test_reads_picture_cover_and_followers_with_the_page_token(self):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers["Authorization"]
            seen["fields"] = request.url.params["fields"]
            return httpx.Response(200, json={
                "id": "111", "picture": {"data": {"url": "https://fb/p.jpg"}},
                "cover": {"source": "https://fb/c.jpg", "offset_y": 140}, "followers_count": 12,
                "link": "https://www.facebook.com/x",
            })

        self.assertEqual(self.fetch(handler), {
            "picture_url": "https://fb/p.jpg", "cover_url": "https://fb/c.jpg", "cover_offset_y": 100,
            "followers": 12, "link": "https://www.facebook.com/x",
        })
        self.assertEqual(seen["auth"], "Bearer tok")
        self.assertIn("cover{source,offset_y}", seen["fields"])

    def test_page_without_cover_and_unsafe_links(self):
        look = self.fetch(lambda r: httpx.Response(200, json={
            "picture": {"data": {"url": "javascript:alert(1)"}}, "fan_count": 3, "link": "http://x",
        }))
        self.assertEqual(look, {"picture_url": None, "cover_url": None, "cover_offset_y": 50, "followers": 3, "link": None})

    def test_any_failure_returns_none(self):
        self.assertIsNone(self.fetch(lambda r: httpx.Response(400, json={"error": {"code": 190}})))
        self.assertIsNone(self.fetch(lambda r: httpx.Response(200, content=b"not json")))

        def down(request):
            raise httpx.ConnectError("down")

        self.assertIsNone(self.fetch(down))
