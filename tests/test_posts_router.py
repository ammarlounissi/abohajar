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
from app.models import PostStatus
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

    def add_post(self, status=PostStatus.DRAFT, platforms="facebook", **kw):
        with self.Session() as db:
            post = models.Post(
                factory_id=1, media_type="text", media_urls=[], description="نص", status=status,
                platforms=platforms, **kw,
            )
            db.add(post)
            db.commit()
            return post.id


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
        post_id = self.add_post(PostStatus.FAILED, platforms="facebook,instagram")
        with self.Session() as db:
            db.add(models.PostDelivery(post_id=post_id, platform="facebook", status="published", remote_id="F1", attempts=1))
            db.add(models.PostDelivery(post_id=post_id, platform="instagram", status="failed", error="(#9004) x", attempts=1))
            db.commit()
        body = self.client.get(f"/api/posts/{post_id}").json()
        states = {d["platform"]: (d["status"], d["remote_id"]) for d in body["deliveries"]}
        self.assertEqual(states, {"facebook": ("published", "F1"), "instagram": ("failed", None)})


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

    def test_unknown_post_and_missing_platforms(self):
        self.assertEqual(self.client.post("/api/posts/9999/publish").status_code, 404)
        post_id = self.add_post(PostStatus.DRAFT, platforms=None)
        self.assertEqual(self.client.post(f"/api/posts/{post_id}/publish").status_code, 422)
        self.bg.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
