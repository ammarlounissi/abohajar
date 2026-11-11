"""Facebook Login مقسَّم حسب الخدمة: أذونات الخدمة وحدها، وصفحة واحدة لكل مستخدم."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.time import utc_now
from app.routers import auth
from app.services import facebook_services

SERVICE = "facebook-reels"
SCOPES = ["pages_show_list", "pages_manage_posts", "pages_read_engagement", "pages_manage_engagement"]
URL = f"/auth/facebook/services/{SERVICE}"


class FacebookServiceTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        app = FastAPI()
        app.include_router(auth.router, prefix="/auth")

        def _db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = _db
        self.app = app
        self.patches = [
            patch.object(settings, "META_APP_ID", "1"),
            patch.object(settings, "META_APP_SECRET", "secret"),
            patch.object(settings, "BASE_URL", "http://localhost:8000"),
            patch.object(settings, "FACEBOOK_SUCCESS_REDIRECT_URL", "https://app.test/connect/done/"),
        ]
        for p in self.patches:
            p.start()
        # ما يردّ به فيسبوك في هذا الاختبار
        self.granted = list(SCOPES)
        self.fb_pages = [{"id": "11111111", "name": "صفحتي", "access_token": "page-tok-1"}]
        self.graph_calls = []

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def client(self):
        return TestClient(self.app, follow_redirects=False)

    async def fake_get(self, client, path, **params):
        self.graph_calls.append(path)
        if path == "oauth/access_token":
            return {"access_token": "user-tok"}
        if path == "me":
            return {"id": "10", "name": "Owner"}
        if path == "me/permissions":
            return {"data": [{"permission": s, "status": "granted"} for s in self.granted]
                    + [{"permission": "email", "status": "declined"}]}
        if path == "me/accounts":
            return {"data": self.fb_pages}
        page = next((p for p in self.fb_pages if p["id"] == path), None)   # /{page_id}
        if page and params.get("access_token") == "user-tok":
            return page
        raise AssertionError(f"طلب Graph غير متوقع: {path}")

    def callback(self, client, service=SERVICE):
        state = auth._make_state(service=service)
        client.cookies.set("hb_oauth", state.split(".")[1])
        with patch.object(auth, "_graph_get", self.fake_get):
            return client.get(f"/auth/facebook/callback?code=c&state={state}")

    def location(self, res):
        self.assertEqual(res.status_code, 303, res.text)
        return {k: v[0] for k, v in parse_qs(urlsplit(res.headers["location"]).query).items()}

    def channels(self):
        with self.Session() as db:
            return [(c.page_id, c.page_access_token) for c in db.query(models.SocialChannel).order_by(models.SocialChannel.id)]

    # ───── بدء الدخول: أذونات الخدمة وحدها ─────

    def test_login_requests_only_the_service_scopes(self):
        res = self.client().get(f"/auth/facebook/login?service={SERVICE}")
        self.assertEqual(res.status_code, 307)
        query = parse_qs(urlsplit(res.headers["location"]).query)
        self.assertEqual(query["scope"][0].split(","), SCOPES)

    def test_login_without_service_requests_identity_only(self):
        res = self.client().get("/auth/facebook/login")
        scope = parse_qs(urlsplit(res.headers["location"]).query)["scope"][0]
        self.assertEqual(scope, "public_profile,email")
        self.assertNotIn("pages_", scope)

    def test_unknown_service_is_404(self):
        self.assertEqual(self.client().get("/auth/facebook/login?service=nope").status_code, 404)
        c = self.client()
        self.callback(c)
        self.assertEqual(c.get("/auth/facebook/services/nope").status_code, 404)

    def test_state_service_cannot_be_swapped(self):
        c = self.client()
        state = auth._make_state(service=SERVICE)
        c.cookies.set("hb_oauth", state.split(".")[1])
        parts = state.split(".")
        parts[3] = ""                                # محاولة إسقاط الخدمة من state موقَّع
        self.assertEqual(c.get(f"/auth/facebook/callback?code=c&state={'.'.join(parts)}").status_code, 400)

    # ───── صفحة واحدة ممنوحة: تُربط مباشرة ─────

    def test_single_page_is_linked_and_becomes_the_service_page(self):
        c = self.client()
        self.assertEqual(self.location(self.callback(c)), {"facebook": "connected", "service": SERVICE})
        status = c.get(URL).json()
        self.assertEqual(status["scopes"], SCOPES)
        self.assertEqual(status["page"], {"page_id": "11111111", "page_name": "صفحتي", "is_active": True})
        self.assertEqual(status["candidates"], [])
        self.assertEqual(self.channels(), [("11111111", "page-tok-1")])
        self.assertNotIn("subscribed_apps", " ".join(self.graph_calls))

    def test_status_requires_login(self):
        self.assertEqual(self.client().get(URL).status_code, 401)
        self.assertEqual(self.client().post(f"{URL}/select", json={"page_id": "1"}).status_code, 401)

    # ───── أكثر من صفحة ممنوحة: لا يُربط شيء حتى يختار المستخدم واحدة ─────

    def grant_two_pages(self):
        self.fb_pages = [
            {"id": "11111111", "name": "الأولى", "access_token": "page-tok-1"},
            {"id": "22222222", "name": "الثانية", "access_token": "page-tok-2"},
        ]

    def test_many_pages_link_nothing_until_one_is_chosen(self):
        self.grant_two_pages()
        c = self.client()
        self.assertEqual(self.location(self.callback(c)), {"facebook": "select", "service": SERVICE})
        self.assertEqual(self.channels(), [])                      # لم تُربط أي صفحة
        res = c.get(URL)
        self.assertIsNone(res.json()["page"])
        self.assertEqual(res.json()["candidates"], [
            {"page_id": "11111111", "page_name": "الأولى"}, {"page_id": "22222222", "page_name": "الثانية"},
        ])
        self.assertNotIn("tok", res.text)                           # لا توكن في الرد
        with self.Session() as db:                                  # ولا توكنات صفحات في قاعدة البيانات
            self.assertNotIn("page-tok", db.query(models.ServiceConnection).one().pending_pages)

        with patch.object(auth, "_graph_get", self.fake_get):
            res = c.post(f"{URL}/select", json={"page_id": "22222222"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["page"]["page_id"], "22222222")
        self.assertEqual(res.json()["candidates"], [])
        self.assertEqual(self.channels(), [("22222222", "page-tok-2")])   # المختارة وحدها
        with self.Session() as db:
            connection = db.query(models.ServiceConnection).one()
            self.assertIsNone(connection.pending_user_token)        # مُحي التوكن المؤقت
            self.assertIsNone(connection.pending_pages)

    def test_cannot_select_a_page_that_was_not_granted(self):
        self.grant_two_pages()
        c = self.client()
        self.callback(c)
        with patch.object(auth, "_graph_get", self.fake_get):
            self.assertEqual(c.post(f"{URL}/select", json={"page_id": "99999999"}).status_code, 422)
        self.assertEqual(self.channels(), [])

    def test_selection_expires(self):
        self.grant_two_pages()
        c = self.client()
        self.callback(c)
        with self.Session() as db:
            db.query(models.ServiceConnection).one().pending_at = (
                utc_now() - facebook_services.PENDING_MAX_AGE - timedelta(seconds=1)
            )
            db.commit()
        with patch.object(auth, "_graph_get", self.fake_get):
            self.assertEqual(c.post(f"{URL}/select", json={"page_id": "11111111"}).status_code, 409)
        self.assertEqual(c.get(URL).json()["candidates"], [])
        with self.Session() as db:
            self.assertIsNone(db.query(models.ServiceConnection).one().pending_user_token)

    def test_cancel_selection_erases_the_pending_token(self):
        self.grant_two_pages()
        c = self.client()
        self.callback(c)
        res = c.delete(f"{URL}/selection")
        self.assertEqual((res.status_code, res.json()["candidates"]), (200, []))
        with self.Session() as db:
            self.assertEqual(db.query(models.ServiceConnection).count(), 0)

    def test_other_user_cannot_use_my_pending_selection(self):
        self.grant_two_pages()
        mine, other = self.client(), self.client()
        self.callback(mine)
        other.post("/auth/register", json={"email": "b@x.com", "password": "secret-pass"})
        self.assertEqual(other.get(URL).json()["candidates"], [])
        with patch.object(auth, "_graph_get", self.fake_get):
            self.assertEqual(other.post(f"{URL}/select", json={"page_id": "11111111"}).status_code, 409)

    # ───── إعادة الربط بصفحة أخرى: الخدمة تبقى على صفحة واحدة ─────

    def test_reconnect_with_another_page_moves_the_service(self):
        c = self.client()
        self.callback(c)
        self.fb_pages = [{"id": "22222222", "name": "الثانية", "access_token": "page-tok-2"}]
        self.callback(c)
        self.assertEqual(c.get(URL).json()["page"]["page_id"], "22222222")
        with self.Session() as db:
            self.assertEqual(db.query(models.ServiceConnection).count(), 1)

    # ───── الرفض ─────

    def test_declined_permission_links_nothing(self):
        self.granted = SCOPES[:3]                                  # رفض pages_manage_engagement
        c = self.client()
        self.assertEqual(
            self.location(self.callback(c)),
            {"facebook": "error", "service": SERVICE, "reason": "missing_permissions"},
        )
        self.assertEqual(self.channels(), [])
        self.assertIsNone(c.get(URL).json()["page"])

    def test_no_page_granted(self):
        self.fb_pages = []
        self.assertEqual(self.location(self.callback(self.client()))["reason"], "no_pages")

    def test_cancelled_dialog_keeps_the_service_in_the_redirect(self):
        state = auth._make_state(service=SERVICE)
        res = self.client().get(f"/auth/facebook/callback?error=access_denied&state={state}")
        self.assertEqual(
            self.location(res), {"facebook": "error", "service": SERVICE, "reason": "access_denied"},
        )

    # ───── فك الارتباط وحذف البيانات ─────

    def test_unlinking_the_page_disconnects_the_service(self):
        c = self.client()
        self.callback(c)
        self.assertEqual(c.delete("/auth/facebook/pages/11111111").json()["result"], "deleted")
        self.assertIsNone(c.get(URL).json()["page"])
        with self.Session() as db:
            self.assertEqual(db.query(models.ServiceConnection).count(), 0)

    def test_facebook_data_deletion_removes_connections(self):
        self.grant_two_pages()
        c = self.client()
        self.callback(c)                                           # اختيار معلّق يحمل توكن المستخدم
        res = c.post("/auth/facebook/delete", json={"user_id": "10"})
        self.assertEqual(res.status_code, 200, res.text)
        with self.Session() as db:
            self.assertEqual(db.query(models.ServiceConnection).count(), 0)
            self.assertEqual(db.query(models.User).count(), 0)

    def test_facebook_deauthorize_removes_linked_page_and_connection(self):
        c = self.client()
        self.callback(c)
        res = c.post("/auth/facebook/deauthorize", json={"user_id": "10"})
        self.assertEqual(res.json()["deleted_pages"], 1)
        with self.Session() as db:
            self.assertEqual(db.query(models.ServiceConnection).count(), 0)
            self.assertEqual(db.query(models.SocialChannel).count(), 0)


if __name__ == "__main__":
    unittest.main()
