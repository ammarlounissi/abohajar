"""مسارات /api/posts الجديدة عبر TestClient: القوائم، تفاصيل المنشور، والنشر اليدوي.
تطبيق مصغّر (الراوتر فقط) وقاعدة SQLite في الذاكرة، والنشر الفعلي في الخلفية مُستبدل بدالة وهمية."""
import unittest
from datetime import datetime
from unittest import mock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.database import Base, get_db
from app.core.security import require_admin
from app.models import PostApproval, PostStatus
from app.routers import posts as posts_router
from app.services import errors, post_publisher

_STATUS = {errors.NotFoundError: 404, errors.DuplicateError: 400, errors.ConflictError: 409, errors.InvalidInputError: 422}


class RouterCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add(models.Factory(id=1, name="مصنع", phone_number="213550000000"))
            db.add(models.Factory(id=2, name="مصنع آخر", phone_number="213550000001"))
            db.add(models.SocialChannel(id=1, factory_id=1, platform="facebook", name="صفحة", remote_id="111111"))
            db.add(models.SocialChannel(id=2, factory_id=1, platform="instagram", name="حساب", remote_id="222222"))
            db.add(models.SocialChannel(
                id=3, factory_id=1, platform="facebook", name="صفحة معطّلة", remote_id="333333", is_active=False,
            ))
            db.add(models.SocialChannel(
                id=4, factory_id=2, platform="facebook", name="صفحة أخرى", remote_id="444444",
            ))
            db.commit()

        app = FastAPI()
        app.include_router(posts_router.router)

        @app.exception_handler(errors.ServiceError)
        async def handler(request: Request, exc: errors.ServiceError):
            code = next((c for cls, c in _STATUS.items() if isinstance(exc, cls)), 400)
            return JSONResponse(status_code=code, content={"detail": str(exc)})

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[require_admin] = lambda: None
        self.client = TestClient(app)

    def add_post(self, status=PostStatus.DRAFT, channels=(1,), **kw):
        """channels: معرّفات القنوات الداخلية (فارغة = منشور بلا وجهات)."""
        with self.Session() as db:
            post = models.Post(
                factory_id=1, media_type="text", media_urls=[], description="نص", status=status, **kw,
            )
            db.add(post)
            db.commit()
            for channel_id in channels:
                db.add(models.PostDelivery(post_id=post.id, channel_id=channel_id, status="pending", attempts=0))
            db.commit()
            return post.id

    def add_product(self, sku, category_code, factory_id=1):
        with self.Session() as db:
            product = models.Product(
                sku=sku, category_code=category_code, title=f"منتج {sku}",
                commercial_name=f"اسم {sku}", tags=["حجر"], sizes="20x30",
                price=100, primary_media_url=f"https://hadjretbladi.com/{sku}/1.jpg",
                factory_id=factory_id,
            )
            db.add(product)
            db.commit()


class ListRoutesTests(RouterCase):
    def test_published_and_unpublished(self):
        pub = self.add_post(PostStatus.PUBLISHED, published_at=datetime(2026, 10, 17))
        draft = self.add_post(PostStatus.DRAFT)
        failed = self.add_post(PostStatus.FAILED)
        archived = self.add_post(PostStatus.ARCHIVED)

        r = self.client.get("/api/posts/published")
        self.assertEqual(r.status_code, 200)       # لم تُفسَّر "published" كرقم
        self.assertEqual([p["id"] for p in r.json()], [pub])

        r = self.client.get("/api/posts/unpublished")
        self.assertEqual({p["id"] for p in r.json()}, {draft, failed})
        self.assertNotIn(archived, {p["id"] for p in r.json()})

        self.assertEqual(len(self.client.get("/api/posts/").json()), 4)

    def test_limit_and_offset(self):
        for _ in range(3):
            self.add_post(PostStatus.DRAFT)
        self.assertEqual(len(self.client.get("/api/posts/unpublished?limit=2").json()), 2)
        self.assertEqual(len(self.client.get("/api/posts/unpublished?limit=2&offset=2").json()), 1)
        self.assertEqual(self.client.get("/api/posts/unpublished?limit=0").status_code, 422)

    def test_detail_includes_deliveries(self):
        post_id = self.add_post(PostStatus.FAILED, channels=(1, 2))
        with self.Session() as db:
            first, second = db.query(models.PostDelivery).order_by(models.PostDelivery.id).all()
            first.status, first.remote_id, first.attempts = "published", "F1", 1
            second.status, second.error, second.attempts = "failed", "(#9004) x", 1
            db.commit()
        body = self.client.get(f"/api/posts/{post_id}").json()
        states = {d["platform"]: (d["channel_id"], d["channel_remote_id"], d["status"], d["remote_id"])
                  for d in body["deliveries"]}
        self.assertEqual(states, {"facebook": (1, "111111", "published", "F1"),
                                  "instagram": (2, "222222", "failed", None)})
        self.assertNotIn("platforms", body)


class ChannelSelectionTests(RouterCase):
    BODY = {"factory_id": 1, "media_type": "text", "description": "نص"}

    def test_create_with_channel_ids(self):
        r = self.client.post("/api/posts/", json={**self.BODY, "channel_ids": [2, 1, 2]})
        self.assertEqual(r.status_code, 201)
        self.assertEqual([(d["channel_id"], d["platform"], d["status"]) for d in r.json()["deliveries"]],
                         [(2, "instagram", "pending"), (1, "facebook", "pending")])

    def test_create_without_channels_is_allowed(self):
        r = self.client.post("/api/posts/", json=self.BODY)
        self.assertEqual((r.status_code, r.json()["deliveries"]), (201, []))

    def test_create_with_bad_channels_creates_nothing(self):
        self.assertEqual(self.client.post("/api/posts/", json={**self.BODY, "channel_ids": [1, 9999]}).status_code, 404)
        self.assertEqual(self.client.post("/api/posts/", json={**self.BODY, "channel_ids": [3]}).status_code, 422)  # معطّلة
        self.assertEqual(self.client.post("/api/posts/", json={**self.BODY, "channel_ids": ["x"]}).status_code, 422)
        self.assertEqual(self.client.get("/api/posts/").json(), [])

    def test_channels_must_belong_to_the_post_factory(self):
        self.assertEqual(
            self.client.post("/api/posts/", json={**self.BODY, "channel_ids": [4]}).status_code, 422,
        )
        post_id = self.add_post(PostStatus.DRAFT, channels=(1,))
        self.assertEqual(self.client.patch(
            f"/api/posts/{post_id}", json={"factory_id": 2},
        ).status_code, 422)
        self.assertEqual(self.client.post("/api/posts/prepare/all", json={
            "factory_id": 1, "channel_ids": [4],
        }).status_code, 422)

    def test_patch_replaces_channels(self):
        post_id = self.add_post(PostStatus.DRAFT, channels=(1,))
        r = self.client.patch(f"/api/posts/{post_id}", json={"channel_ids": [2]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual([d["channel_id"] for d in r.json()["deliveries"]], [2])
        r = self.client.patch(f"/api/posts/{post_id}", json={"description": "جديد"})      # بلا channel_ids: لا تتغير
        self.assertEqual([d["channel_id"] for d in r.json()["deliveries"]], [2])

    def test_patch_channels_of_published_post_conflicts(self):
        post_id = self.add_post(PostStatus.PUBLISHED, channels=(1,))
        self.assertEqual(self.client.patch(f"/api/posts/{post_id}", json={"channel_ids": [2]}).status_code, 409)


class PreparedPostRoutesTests(RouterCase):
    def test_prepare_by_sku_creates_approved_deliveries_and_platform_captions(self):
        self.add_product("B001", "B")
        response = self.client.post("/api/posts/prepare/by-sku", json={
            "factory_id": 1, "channel_ids": [2, 1], "skus": ["b001"],
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["count"], 1)
        with self.Session() as db:
            post = db.get(models.Post, response.json()["post_ids"][0])
            self.assertEqual(post.status, PostStatus.APPROVED)
            self.assertEqual(post.approval, PostApproval.APPROVED)
            self.assertIsNotNone(post.approved_at)
            self.assertEqual(post.sku, "B001")
            self.assertEqual([delivery.channel_id for delivery in post.deliveries], [2, 1])
            captions = {delivery.platform: delivery.caption for delivery in post.deliveries}
            self.assertIn("رمز المنتج: B001", captions["instagram"])
            self.assertIn("https://wa.me/", captions["facebook"])
            self.assertEqual(post.media_urls, ["https://hadjretbladi.com/B001/1.jpg"])

    def test_prepare_all_products(self):
        self.add_product("B001", "B")
        self.add_product("PTK002", "PTK")
        response = self.client.post("/api/posts/prepare/all", json={
            "factory_id": 1, "channel_ids": [1], "times": [8, 14],
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["count"], 2)
        with self.Session() as db:
            posts = db.query(models.Post).order_by(models.Post.id).all()
            self.assertEqual({post.sku for post in posts}, {"B001", "PTK002"})
            self.assertTrue(all(post.status == PostStatus.APPROVED for post in posts))

    def test_prepare_by_multiple_categories_and_missing_sku(self):
        self.add_product("B001", "B")
        self.add_product("PTK002", "PTK")
        self.add_product("C003", "C")
        response = self.client.post("/api/posts/prepare/by-category", json={
            "factory_id": 1, "channel_ids": [1], "category_codes": ["b", "PTK"],
            "times": "8,14,18",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["count"], 2)
        missing = self.client.post("/api/posts/prepare/by-sku", json={
            "factory_id": 1, "channel_ids": [1], "skus": ["B999"],
        })
        self.assertEqual(missing.status_code, 404)

    def _scheduled(self):
        with self.Session() as db:
            return [p.scheduled_at for p in db.query(models.Post).order_by(models.Post.scheduled_at)]

    def test_prepare_all_spreads_posts_over_daily_times_stored_as_utc(self):
        for i in range(3):
            self.add_product(f"B00{i + 1}", "B")
        response = self.client.post("/api/posts/prepare/all", json={
            "factory_id": 1, "channel_ids": [1], "times": [8, 14], "start_date": "2030-01-01",
        })
        self.assertEqual(response.status_code, 201)
        # الجزائر UTC+1: 08:00 -> 07:00Z و14:00 -> 13:00Z
        self.assertEqual(self._scheduled(), [
            datetime(2030, 1, 1, 7), datetime(2030, 1, 1, 13), datetime(2030, 1, 2, 7),
        ])
        body = response.json()
        self.assertTrue(body["first_scheduled_at"].startswith("2030-01-01T07:00"))
        self.assertTrue(body["last_scheduled_at"].startswith("2030-01-02T07:00"))

    def test_second_batch_continues_the_queue_instead_of_colliding(self):
        self.add_product("B001", "B")
        self.add_product("PTK002", "PTK")
        payload = {"factory_id": 1, "channel_ids": [1], "times": [8, 14], "start_date": "2030-01-01"}
        self.client.post("/api/posts/prepare/by-category", json={**payload, "category_codes": ["B"]})
        self.client.post("/api/posts/prepare/by-category", json={**payload, "category_codes": ["PTK"]})
        self.assertEqual(self._scheduled(), [datetime(2030, 1, 1, 7), datetime(2030, 1, 1, 13)])

    def test_prepare_all_and_category_require_times(self):
        self.add_product("B001", "B")
        self.assertEqual(self.client.post("/api/posts/prepare/all", json={
            "factory_id": 1, "channel_ids": [1]}).status_code, 422)
        self.assertEqual(self.client.post("/api/posts/prepare/by-category", json={
            "factory_id": 1, "channel_ids": [1], "category_codes": ["B"]}).status_code, 422)
        self.assertEqual(self.client.post("/api/posts/prepare/all", json={
            "factory_id": 1, "channel_ids": [1], "times": [25]}).status_code, 422)

    def test_by_sku_without_times_stays_immediate_and_start_date_needs_times(self):
        self.add_product("B001", "B")
        r = self.client.post("/api/posts/prepare/by-sku", json={"factory_id": 1, "channel_ids": [1], "skus": ["B001"]})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self._scheduled(), [None])
        bad = self.client.post("/api/posts/prepare/by-sku", json={
            "factory_id": 1, "channel_ids": [1], "skus": ["B001"], "start_date": "2030-01-01"})
        self.assertEqual(bad.status_code, 422)


class PublishNowTests(RouterCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(post_publisher, "publish_claimed_in_background", mock.AsyncMock())
        self.bg = p.start()
        self.addCleanup(p.stop)

    def test_publish_claims_and_schedules_background(self):
        post_id = self.add_post(PostStatus.DRAFT)
        r = self.client.post(f"/api/posts/{post_id}/publish")
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.json()["status"], "publishing")
        self.bg.assert_awaited_once_with(post_id)

    def test_second_publish_conflicts(self):
        post_id = self.add_post(PostStatus.APPROVED)
        self.assertEqual(self.client.post(f"/api/posts/{post_id}/publish").status_code, 202)
        self.assertEqual(self.client.post(f"/api/posts/{post_id}/publish").status_code, 409)
        self.assertEqual(self.bg.await_count, 1)

    def test_published_post_rejected(self):
        post_id = self.add_post(PostStatus.PUBLISHED)
        self.assertEqual(self.client.post(f"/api/posts/{post_id}/publish").status_code, 409)
        self.bg.assert_not_awaited()

    def test_unknown_post_and_missing_channels(self):
        self.assertEqual(self.client.post("/api/posts/9999/publish").status_code, 404)
        post_id = self.add_post(PostStatus.DRAFT, channels=())
        self.assertEqual(self.client.post(f"/api/posts/{post_id}/publish").status_code, 422)
        self.bg.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()


class FactoryReelPostTests(RouterCase):
    URL = "/api/posts/from-factory-video"

    def add_jobs(self, factory_id=1, video_status="succeeded", chat_status="succeeded"):
        with self.Session() as db:
            video = models.FactoryVideoJob(
                factory_id=factory_id, source="library", template="cinematic_30", duration=30,
                status=video_status, video_url="https://api.example.com/api/media/factories/1/a.mp4",
            )
            chat = models.FactoryDescriptionJob(
                factory_id=factory_id, prompt="p", messages=[], model="m", status=chat_status,
                response="  وصف تسويقي للمصنع  ",
            )
            db.add_all([video, chat])
            db.commit()
            return video.id, chat.id

    def test_creates_approved_reel_with_all_active_channels(self):
        video_id, chat_id = self.add_jobs()
        r = self.client.post(self.URL, json={"factory_id": 1, "video_id": video_id, "chat_id": chat_id})
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["media_type"], "reel")
        self.assertEqual(body["media_urls"], ["https://api.example.com/api/media/factories/1/a.mp4"])
        self.assertEqual((body["status"], body["approval"]), ("approved", "approved"))
        self.assertIsNone(body["scheduled_at"])
        self.assertEqual(sorted(d["channel_id"] for d in body["deliveries"]), [1, 2])   # 3 معطّلة و4 لمصنع آخر
        self.assertTrue(body["description"].startswith("وصف تسويقي للمصنع\n"))
        self.assertIn("213550000000", body["description"])
        self.assertIn("#حجرة_بلادي", body["description"])

    def test_rejects_extra_params(self):
        video_id, chat_id = self.add_jobs()
        r = self.client.post(self.URL, json={"factory_id": 1, "video_id": video_id, "chat_id": chat_id, "x": 1})
        self.assertEqual(r.status_code, 422)

    def test_unfinished_jobs_are_conflict(self):
        video_id, chat_id = self.add_jobs(video_status="running")
        self.assertEqual(self.client.post(
            self.URL, json={"factory_id": 1, "video_id": video_id, "chat_id": chat_id}).status_code, 409)
        video_id, chat_id = self.add_jobs(chat_status="queued")
        self.assertEqual(self.client.post(
            self.URL, json={"factory_id": 1, "video_id": video_id, "chat_id": chat_id}).status_code, 409)

    def test_jobs_of_another_factory_are_not_found(self):
        video_id, chat_id = self.add_jobs(factory_id=2)
        r = self.client.post(self.URL, json={"factory_id": 1, "video_id": video_id, "chat_id": chat_id})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.get("/api/posts/").json(), [])

    def test_factory_without_active_channels_is_rejected_without_post(self):
        with self.Session() as db:
            db.query(models.SocialChannel).filter(models.SocialChannel.factory_id == 1).update({"is_active": False})
            db.commit()
        video_id, chat_id = self.add_jobs()
        r = self.client.post(self.URL, json={"factory_id": 1, "video_id": video_id, "chat_id": chat_id})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.client.get("/api/posts/").json(), [])


class ExtractDescriptionTests(unittest.TestCase):
    def test_fenced_json_object(self):
        from app.services.factory_reel_posts import extract_description
        raw = '```json\n{"description": "**حجر** ديكور فاخر\\n✨ جودة عالية", "hashtags": ["#ديكور", "حجر طبيعي"]}\n```'
        text, tags = extract_description(raw)
        self.assertEqual(text, "حجر ديكور فاخر\n✨ جودة عالية")
        self.assertEqual(tags, ["#ديكور", "#حجر", "#طبيعي"])

    def test_plain_text_and_json_string_and_list(self):
        from app.services.factory_reel_posts import extract_description
        self.assertEqual(extract_description("## عنوان\n- نقطة **مهمة**")[0], "عنوان\nنقطة مهمة")
        self.assertEqual(extract_description('"نص داخل JSON"')[0], "نص داخل JSON")
        self.assertEqual(extract_description('["سطر 1", "سطر 2"]')[0], "سطر 1\n\nسطر 2")
        self.assertEqual(extract_description('{"a": "x", "b": "y"}')[0], "x\n\ny")

    def test_keeps_hashtags_in_body(self):
        from app.services.factory_reel_posts import extract_description
        self.assertEqual(extract_description('{"text": "وصف #جزائر"}')[0], "وصف #جزائر")


class FactoryReelJsonResponseTests(FactoryReelPostTests):
    def test_json_response_is_cleaned_before_use(self):
        with self.Session() as db:
            video = models.FactoryVideoJob(
                factory_id=1, source="library", template="t", duration=30, status="succeeded",
                video_url="https://x.example/a.mp4",
            )
            chat = models.FactoryDescriptionJob(
                factory_id=1, prompt="p", messages=[], model="m", status="succeeded",
                response='```json\n{"description": "**وصف** نظيف"}\n```',
            )
            db.add_all([video, chat])
            db.commit()
            ids = (video.id, chat.id)
        r = self.client.post(self.URL, json={"factory_id": 1, "video_id": ids[0], "chat_id": ids[1]})
        self.assertEqual(r.status_code, 201, r.text)
        description = r.json()["description"]
        self.assertTrue(description.startswith("وصف نظيف\n"))
        for junk in ("```", "json", "{", "**"):
            self.assertNotIn(junk, description)
