"""مساحة الزبون: كل صفحة مربوطة بخدمة = مصنع خاص بها يملك مكتبتها، والزبون لا يصل إلا إلى مكتبته."""
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.config import settings
from app.core.database import Base, get_db
import app.services.social  # noqa: F401  (يُستورد أولاً: public_media وحده يدخل حلقة استيراد قائمة)
from app.routers import customer_workspace, public_media
from app.services import (
    customer_workspaces, errors, facebook_services, factory_reel_posts, sessions, social_channels,
)

SERVICE = "facebook-reels"
BASE = f"/api/services/{SERVICE}"
_STATUS = {errors.NotFoundError: 404, errors.ConflictError: 409, errors.InvalidInputError: 422}


def image_bytes(image_format="PNG", size=(40, 30)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, (200, 120, 40)).save(buffer, format=image_format)
    return buffer.getvalue()


class WorkspaceTestBase(unittest.TestCase):
    """تطبيق بمسارات الزبون وقاعدة في الذاكرة ومستخدمان (1 و2). تستعمله اختبارات المكتبة والفيديو."""

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        app = FastAPI()
        app.include_router(customer_workspace.router)
        app.include_router(public_media.router)

        @app.exception_handler(errors.ServiceError)
        async def handle(request: Request, exc: errors.ServiceError):
            code = next((c for cls, c in _STATUS.items() if isinstance(exc, cls)), 400)
            return JSONResponse(status_code=code, content={"detail": str(exc)})

        def _db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _db
        self.app = app
        self.tmp = TemporaryDirectory()
        self.patches = [
            patch.object(settings, "FACTORY_MEDIA_ROOT", Path(self.tmp.name)),
            patch.object(settings, "BASE_URL", "http://testserver"),
        ]
        for p in self.patches:
            p.start()
        with self.Session() as db:
            db.add_all([
                models.User(id=1, email="a@example.com", name="A", facebook_id="fb-a"),
                models.User(id=2, email="b@example.com", name="B", facebook_id="fb-b"),
            ])
            db.commit()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    # ───── أدوات ─────

    def client(self, user_id=None):
        client = TestClient(self.app)
        if user_id is not None:
            client.cookies.set(settings.SESSION_COOKIE_NAME, sessions.make_token(user_id))
        return client

    def link(self, user_id, page_id="111", name="صفحتي"):
        """ما يفعله مسار الربط في auth: ربط الصفحة، جعلها صفحة الخدمة، وإنشاء مساحتها."""
        with self.Session() as db:
            channel = social_channels.link_facebook_page(
                db, user_id=user_id, page_id=page_id, page_name=name, page_token="tok",
                owner_fbid=f"fb-{user_id}", owner_name="Owner",
            )
            self.assertIsNotNone(channel)
            facebook_services.activate(db, user_id=user_id, service=SERVICE, channel=channel)
            customer_workspaces.ensure_workspace(db, user_id=user_id, channel=channel)
            db.commit()

    def unlink(self, user_id, page_id="111"):
        with self.Session() as db:
            channel = social_channels.get_user_facebook_page(db, user_id, page_id)
            return social_channels.remove_user_facebook_page(db, channel)

    def upload(self, client, name="photo.png", data=None, content_type="image/png"):
        return client.post(
            f"{BASE}/media", files={"file": (name, data if data is not None else image_bytes(), content_type)},
        )

    def factory_of(self, user_id, page_id="111"):
        with self.Session() as db:
            row = db.query(models.CustomerWorkspace).filter_by(user_id=user_id, page_id=page_id).one()
            return row.factory_id


class CustomerWorkspaceTests(WorkspaceTestBase):

    # ───── الوصول ─────

    def test_requires_login(self):
        self.assertEqual(self.client().get(f"{BASE}/workspace").status_code, 401)
        self.assertEqual(self.client().get(f"{BASE}/media").status_code, 401)
        self.assertEqual(self.upload(self.client()).status_code, 401)
        self.assertEqual(self.client().delete(f"{BASE}/media/1").status_code, 401)

    def test_unknown_service_is_404(self):
        self.link(1)
        self.assertEqual(self.client(1).get("/api/services/nope/workspace").status_code, 404)

    def test_user_without_linked_page_gets_409(self):
        client = self.client(1)
        self.assertEqual(client.get(f"{BASE}/workspace").status_code, 409)
        self.assertEqual(client.get(f"{BASE}/media").status_code, 409)
        self.assertEqual(self.upload(client).status_code, 409)
        with self.Session() as db:
            self.assertEqual(db.query(models.Factory).count(), 0)

    # ───── الصفحة = مصنع ─────

    def test_linking_a_page_creates_its_own_customer_factory(self):
        self.link(1, name="مخبزة النور")
        with self.Session() as db:
            factory = db.query(models.Factory).one()
            channel = db.query(models.SocialChannel).one()
            self.assertEqual((factory.kind, factory.name, factory.phone_number), ("customer", "مخبزة النور", None))
            self.assertEqual(channel.factory_id, factory.id)
        body = self.client(1).get(f"{BASE}/workspace").json()
        self.assertEqual(
            (body["page_id"], body["page_name"], body["images_count"], body["videos_count"]),
            ("111", "مخبزة النور", 0, 0),
        )
        self.assertEqual(body["max_images"], customer_workspaces.MAX_IMAGES)
        self.assertNotIn("factory_id", body)

    def test_connection_made_before_this_feature_gets_a_workspace_on_first_use(self):
        with self.Session() as db:   # ربط قديم: قناة وارتباط خدمة بلا مساحة
            channel = social_channels.link_facebook_page(
                db, user_id=1, page_id="111", page_name="قديمة", page_token="tok",
                owner_fbid="fb-1", owner_name="Owner",
            )
            facebook_services.activate(db, user_id=1, service=SERVICE, channel=channel)
            db.commit()
        self.assertEqual(self.client(1).get(f"{BASE}/workspace").status_code, 200)
        with self.Session() as db:
            self.assertEqual(db.query(models.SocialChannel).one().factory_id, self.factory_of(1))

    def test_short_page_name_still_makes_a_valid_factory_name(self):
        self.link(1, name="ن")
        with self.Session() as db:
            self.assertGreaterEqual(len(db.query(models.Factory).one().name), 2)

    def test_page_assigned_to_a_supplier_factory_is_adopted_not_replaced(self):
        with self.Session() as db:
            db.add(models.Factory(id=7, name="مصنع الحجر", phone_number="213550000000"))
            db.add(models.SocialChannel(
                platform="facebook", name="صفحة المصنع", remote_id="111", page_id="111", factory_id=7,
            ))
            db.commit()
        self.link(1)
        self.assertEqual(self.factory_of(1), 7)
        with self.Session() as db:
            self.assertEqual(db.query(models.Factory).count(), 1)
            self.assertEqual(db.query(models.SocialChannel).one().factory_id, 7)

    # ───── المكتبة ─────

    def test_upload_list_serve_and_delete_an_image(self):
        self.link(1)
        client = self.client(1)
        res = self.upload(client)
        self.assertEqual(res.status_code, 201, res.text)
        item = res.json()
        self.assertEqual(item["media_type"], "image")
        self.assertNotIn("factory_id", item)
        factory_id = self.factory_of(1)
        self.assertTrue(item["url"].startswith(f"http://testserver/api/media/factories/{factory_id}/"))
        files = list((Path(self.tmp.name) / str(factory_id) / "library").iterdir())
        self.assertEqual(len(files), 1)

        self.assertEqual([m["id"] for m in client.get(f"{BASE}/media").json()], [item["id"]])
        self.assertEqual(client.get(f"{BASE}/workspace").json()["images_count"], 1)
        served = client.get(item["url"].removeprefix("http://testserver"))
        self.assertEqual((served.status_code, served.headers["content-type"]), (200, "image/png"))

        self.assertEqual(client.delete(f"{BASE}/media/{item['id']}").status_code, 204)
        self.assertEqual(client.get(f"{BASE}/media").json(), [])
        self.assertFalse(files[0].exists())

    def test_accepts_jpeg_and_webp(self):
        self.link(1)
        client = self.client(1)
        self.assertEqual(self.upload(client, "a.JPG", image_bytes("JPEG"), "image/jpeg").status_code, 201)
        self.assertEqual(self.upload(client, "b.webp", image_bytes("WEBP"), "image/webp").status_code, 201)

    def test_rejects_what_is_not_a_real_image(self):
        self.link(1)
        client = self.client(1)
        cases = [
            ("vector.svg", b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),   # صيغة غير مقبولة
            ("clip.mp4", b"\x00\x00\x00\x18ftypmp42", "video/mp4"),                            # فيديو
            ("fake.png", b"<script>alert(1)</script>", "image/png"),                           # ليس صورة
            ("renamed.png", image_bytes("JPEG"), "image/png"),                                 # المحتوى يخالف الامتداد
            ("empty.png", b"", "image/png"),
            ("noext", image_bytes(), "image/png"),
        ]
        for name, data, content_type in cases:
            with self.subTest(name=name):
                self.assertEqual(self.upload(client, name, data, content_type).status_code, 422)
        self.assertEqual(client.get(f"{BASE}/media").json(), [])
        self.assertFalse((Path(self.tmp.name) / str(self.factory_of(1)) / "library").exists())

    def test_rejects_oversized_and_over_quota(self):
        self.link(1)
        client = self.client(1)
        with patch.object(customer_workspaces, "MAX_IMAGE_BYTES", 50):
            self.assertEqual(self.upload(client).status_code, 422)
        with patch.object(customer_workspaces, "MAX_IMAGE_PIXELS", 100):
            self.assertEqual(self.upload(client).status_code, 422)
        with patch.object(customer_workspaces, "MAX_IMAGES", 1):
            self.assertEqual(self.upload(client).status_code, 201)
            res = self.upload(client)
            self.assertEqual(res.status_code, 422)
            self.assertIn("الحد الأقصى", res.json()["detail"])

    def test_image_list_pages_newest_first_with_total_in_workspace(self):
        """لوحة الزبون تعرض المكتبة صفحات: skip/limit على قائمة الصور، والعدد الكلي من /workspace."""
        self.link(1)
        client = self.client(1)
        ids = [self.upload(client, name=f"p{i}.png").json()["id"] for i in range(5)]
        newest_first = list(reversed(ids))

        def page(skip, limit):
            response = client.get(f"{BASE}/media", params={"media_type": "image", "skip": skip, "limit": limit})
            self.assertEqual(response.status_code, 200)
            return [item["id"] for item in response.json()]

        self.assertEqual(page(0, 2), newest_first[0:2])
        self.assertEqual(page(2, 2), newest_first[2:4])
        self.assertEqual(page(4, 2), newest_first[4:5])
        self.assertEqual(page(6, 2), [])
        self.assertEqual(client.get(f"{BASE}/workspace").json()["images_count"], 5)

        client.delete(f"{BASE}/media/{newest_first[0]}")
        self.assertEqual(page(0, 2), newest_first[1:3])
        self.assertEqual(client.get(f"{BASE}/workspace").json()["images_count"], 4)

    # ───── العزل بين الزبائن ─────

    def test_users_cannot_see_or_delete_each_others_media(self):
        self.link(1, page_id="111")
        self.link(2, page_id="222")
        mine = self.upload(self.client(1)).json()
        other = self.client(2)
        self.assertEqual(other.get(f"{BASE}/media").json(), [])
        self.assertEqual(other.delete(f"{BASE}/media/{mine['id']}").status_code, 404)
        self.assertEqual(len(self.client(1).get(f"{BASE}/media").json()), 1)
        self.assertNotEqual(self.factory_of(1, "111"), self.factory_of(2, "222"))

    def test_relinking_the_same_page_brings_the_library_back(self):
        self.link(1)
        item = self.upload(self.client(1)).json()
        factory_id = self.factory_of(1)
        self.assertEqual(self.unlink(1), "deleted")
        self.assertEqual(self.client(1).get(f"{BASE}/media").status_code, 409)
        self.link(1)
        self.assertEqual(self.factory_of(1), factory_id)
        self.assertEqual([m["id"] for m in self.client(1).get(f"{BASE}/media").json()], [item["id"]])

    def test_switching_pages_switches_the_library(self):
        self.link(1, page_id="111")
        self.upload(self.client(1))
        self.link(1, page_id="222", name="صفحة ثانية")   # الخدمة تنتقل إلى الصفحة الجديدة
        client = self.client(1)
        self.assertEqual(client.get(f"{BASE}/workspace").json()["page_id"], "222")
        self.assertEqual(client.get(f"{BASE}/media").json(), [])
        self.assertNotEqual(self.factory_of(1, "111"), self.factory_of(1, "222"))

    def test_page_relinked_by_another_user_does_not_inherit_the_library(self):
        self.link(1)
        self.upload(self.client(1))
        with self.Session() as db:   # للصفحة منشور، فيبقى صفها مفكوكاً بدل أن يُحذف
            channel = db.query(models.SocialChannel).one()
            post = models.Post(factory_id=channel.factory_id, media_type="text", media_urls=[])
            post.deliveries.append(models.PostDelivery(channel_id=channel.id))
            db.add(post)
            db.commit()
        self.assertEqual(self.unlink(1), "detached")
        self.link(2)   # مستخدم آخر يربط الصفحة نفسها (الصف القديم ما زال يشير إلى مصنع الأول)
        self.assertNotEqual(self.factory_of(2), self.factory_of(1))
        self.assertEqual(self.client(2).get(f"{BASE}/media").json(), [])
        with self.Session() as db:
            self.assertEqual(db.query(models.SocialChannel).one().factory_id, self.factory_of(2))

    # ───── مصنع بلا رقم هاتف ─────

    def test_reel_caption_has_no_whatsapp_line_without_a_phone(self):
        customer = models.Factory(name="مخبزة النور", kind="customer")
        supplier = models.Factory(name="مصنع", phone_number="213550000000")
        self.assertNotIn("واتساب", factory_reel_posts.build_description(customer, "خبز طازج كل صباح"))
        self.assertNotIn("None", factory_reel_posts.build_description(customer, "خبز طازج كل صباح"))
        self.assertIn("213550000000", factory_reel_posts.build_description(supplier, "حجر طبيعي"))


if __name__ == "__main__":
    unittest.main()
