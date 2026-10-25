"""CRUD قنوات النشر: الخدمة (التطبيع والتكرار) والمسارات عبر TestClient على SQLite في الذاكرة."""
import unittest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.database import Base, get_db
from app.core.security import require_admin
from app.routers import social_channels as channels_router
from app.services import errors
from app.services import social_channels as service

_STATUS = {errors.NotFoundError: 404, errors.DuplicateError: 400, errors.ConflictError: 409, errors.InvalidInputError: 422}


class ServiceTests(unittest.TestCase):
    def test_meta_ids_are_numeric_only(self):
        self.assertEqual(service.normalize_remote_id("facebook", " 123456789012345 "), "123456789012345")
        for bad in ("hadjretbladi", "12ab34567", "123", ""):
            with self.subTest(bad=bad), self.assertRaises(errors.InvalidInputError):
                service.normalize_remote_id("instagram", bad)

    def test_telegram_accepts_numeric_or_username(self):
        self.assertEqual(service.normalize_remote_id("telegram", "-1001234567890"), "-1001234567890")
        self.assertEqual(service.normalize_remote_id("telegram", "@hadjret_bladi"), "@hadjret_bladi")
        for bad in ("hadjret", "@ab", "-12"):
            with self.subTest(bad=bad), self.assertRaises(errors.InvalidInputError):
                service.normalize_remote_id("telegram", bad)

    def test_unknown_platform_rejected(self):
        with self.assertRaises(errors.InvalidInputError):
            service.normalize_remote_id("tiktok", "123456")


class RouterCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add(models.Factory(id=1, name="مصنع", phone_number="213550000000"))
            db.commit()
        app = FastAPI()
        app.include_router(channels_router.router)

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

    def create(self, platform="facebook", name="صفحة", remote_id="123456789012345", factory_id=1, **kw):
        return self.client.post("/api/social-channels/", json={
            "factory_id": factory_id, "platform": platform, "name": name, "remote_id": remote_id, **kw})


class CrudTests(RouterCase):
    def test_create_defaults_to_active(self):
        r = self.create()
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertEqual((body["factory_id"], body["platform"], body["name"], body["remote_id"], body["is_active"]),
                         (1, "facebook", "صفحة", "123456789012345", True))

    def test_create_inactive(self):
        self.assertFalse(self.create(is_active=False).json()["is_active"])

    def test_create_trims_text(self):
        body = self.create(name="  صفحة  ", remote_id=" 123456789 ").json()
        self.assertEqual((body["name"], body["remote_id"]), ("صفحة", "123456789"))

    def test_create_rejects_bad_input(self):
        self.assertEqual(self.create(platform="tiktok").status_code, 422)
        self.assertEqual(self.create(remote_id="not-a-number").status_code, 422)
        self.assertEqual(self.create(name="   ").status_code, 422)
        self.assertEqual(self.create(factory_id=999).status_code, 404)

    def test_duplicate_same_platform_rejected_but_other_platform_allowed(self):
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create().status_code, 400)
        self.assertEqual(self.create(platform="instagram").status_code, 201)

    def test_list_and_filters(self):
        self.create(remote_id="111111111")
        self.create(platform="instagram", remote_id="222222222", is_active=False)
        self.create(platform="telegram", remote_id="@hadjret_bladi")
        get = lambda q="": self.client.get(f"/api/social-channels/{q}").json()
        self.assertEqual(len(get()), 3)
        self.assertEqual([c["platform"] for c in get("?platform=instagram")], ["instagram"])
        self.assertEqual(len(get("?is_active=true")), 2)
        self.assertEqual([c["remote_id"] for c in get("?is_active=false")], ["222222222"])
        self.assertEqual(self.client.get("/api/social-channels/?platform=tiktok").status_code, 422)
        with self.Session() as db:
            db.add(models.Factory(id=2, name="مصنع آخر", phone_number="213550000001"))
            db.commit()
        self.create(remote_id="333333333", factory_id=2)
        self.assertEqual([c["factory_id"] for c in get("?factory_id=2")], [2])
        self.assertEqual(self.client.get("/api/social-channels/?factory_id=0").status_code, 422)

    def test_get_one_and_404(self):
        cid = self.create().json()["id"]
        self.assertEqual(self.client.get(f"/api/social-channels/{cid}").status_code, 200)
        self.assertEqual(self.client.get("/api/social-channels/999").status_code, 404)

    def test_patch_toggles_and_renames(self):
        cid = self.create().json()["id"]
        r = self.client.patch(f"/api/social-channels/{cid}", json={"is_active": False, "name": "جديد"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()["is_active"], r.json()["name"]), (False, "جديد"))
        self.assertEqual(r.json()["remote_id"], "123456789012345")   # لم يُلمس

    def test_patch_remote_id_validated_by_stored_platform_and_unique(self):
        a = self.create(remote_id="111111111").json()["id"]
        self.create(remote_id="222222222")
        self.assertEqual(self.client.patch(f"/api/social-channels/{a}", json={"remote_id": "abc"}).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/social-channels/{a}", json={"remote_id": "222222222"}).status_code, 400)
        # نفس القيمة لنفس القناة ليست تكراراً
        self.assertEqual(self.client.patch(f"/api/social-channels/{a}", json={"remote_id": "111111111"}).status_code, 200)

    def test_patch_cannot_change_platform_or_null_required_fields(self):
        cid = self.create().json()["id"]
        self.client.patch(f"/api/social-channels/{cid}", json={"platform": "instagram"})
        self.assertEqual(self.client.get(f"/api/social-channels/{cid}").json()["platform"], "facebook")
        self.assertEqual(self.client.patch(f"/api/social-channels/{cid}", json={"name": None}).status_code, 422)
        self.assertEqual(self.client.patch(f"/api/social-channels/{cid}", json={"is_active": None}).status_code, 422)
        self.assertEqual(self.client.patch("/api/social-channels/999", json={"name": "x"}).status_code, 404)

    def test_delete(self):
        cid = self.create().json()["id"]
        self.assertEqual(self.client.delete(f"/api/social-channels/{cid}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/social-channels/{cid}").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/social-channels/{cid}").status_code, 404)

    def test_list_active_for_publisher(self):
        self.create(remote_id="111111111")
        self.create(remote_id="222222222", is_active=False)
        with self.Session() as db:
            db.add(models.Factory(id=2, name="مصنع آخر", phone_number="213550000001"))
            db.commit()
        self.create(remote_id="333333333", factory_id=2)
        with self.Session() as db:
            self.assertEqual(
                [c.remote_id for c in service.list_active(db, "facebook", 1)], ["111111111"],
            )

if __name__ == "__main__":
    unittest.main()
