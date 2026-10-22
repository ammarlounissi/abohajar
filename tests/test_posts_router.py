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
            db.add(models.SocialChannel(id=1, platform="facebook", name="صفحة", remote_id="111111"))
            db.add(models.SocialChannel(id=2, platform="instagram", name="حساب", remote_id="222222"))
            db.add(models.SocialChannel(id=3, platform="facebook", name="صفحة معطّلة", remote_id="333333", is_active=False))
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
