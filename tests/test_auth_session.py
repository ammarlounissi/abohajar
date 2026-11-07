"""جلسة المستخدم، وأن /connect لا يعرض لكل مستخدم إلا صفحاته."""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.config import settings
from app.core.database import Base, get_db
from app.routers import auth
from app.services import sessions


class SessionTests(unittest.TestCase):
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
            patch.object(settings, "BASE_URL", "http://localhost:8000"),   # كوكي بلا Secure في TestClient
            patch.object(settings, "FACEBOOK_SUCCESS_REDIRECT_URL", ""),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def client(self):
        return TestClient(self.app, follow_redirects=False)

    def register(self, client, email):
        res = client.post("/auth/register", json={"email": email, "password": "secret-pass", "name": "N"})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["user"]["id"]

    def add_page(self, user_id, page_id):
        with self.Session() as db:
            db.add(models.SocialChannel(
                platform="facebook", name=page_id, remote_id=page_id, page_id=page_id,
                page_name=f"Page {page_id}", user_id=user_id,
            ))
            db.commit()

    def test_status_requires_login(self):
        self.assertEqual(self.client().get("/auth/facebook/status").status_code, 401)
        self.assertEqual(self.client().get("/auth/me").status_code, 401)

    def test_user_sees_only_own_pages(self):
        a, b = self.client(), self.client()
        id_a, id_b = self.register(a, "a@x.com"), self.register(b, "b@x.com")
        self.add_page(id_a, "11111"), self.add_page(id_b, "22222")
        self.assertEqual([p["page_id"] for p in a.get("/auth/facebook/status").json()["pages"]], ["11111"])
        self.assertEqual([p["page_id"] for p in b.get("/auth/facebook/status").json()["pages"]], ["22222"])
        self.assertNotIn('"page_access_token"', a.get("/auth/facebook/status").text)

    def test_login_logout_and_cookie_flags(self):
        c = self.client()
        self.register(c, "a@x.com")
        c.post("/auth/logout")
        c.cookies.clear()
        self.assertEqual(c.post("/auth/login", json={"email": "a@x.com", "password": "wrong-pass"}).status_code, 401)
        res = c.post("/auth/login", json={"email": "a@x.com", "password": "secret-pass"})
        self.assertEqual(res.status_code, 200)
        cookie = res.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertEqual(c.get("/auth/me").json()["user"]["email"], "a@x.com")

    def test_tampered_or_expired_token_rejected(self):
        c = self.client()
        uid = self.register(c, "a@x.com")
        token = sessions.make_token(uid)
        self.assertEqual(sessions.read_token(token), uid)
        self.assertIsNone(sessions.read_token(token[:-2] + "xx"))
        self.assertIsNone(sessions.read_token(sessions.make_token(uid, max_age=-5)))
        forged = f"{uid + 1}." + token.split(".", 1)[1]    # user_id مختلف بتوقيع المستخدم الأصلي
        self.assertIsNone(sessions.read_token(forged))
        c.cookies.clear()                                   # كي لا يُرسل كوكي التسجيل الصحيح بجانبه
        c.cookies.set(settings.SESSION_COOKIE_NAME, forged)
        self.assertEqual(c.get("/auth/me").status_code, 401)

    def test_deactivated_user_loses_session(self):
        c = self.client()
        uid = self.register(c, "a@x.com")
        with self.Session() as db:
            db.get(models.User, uid).is_active = False
            db.commit()
        self.assertEqual(c.get("/auth/me").status_code, 401)

    def test_facebook_callback_links_pages_to_session_user_and_not_to_others(self):
        owner, other = self.client(), self.client()
        id_owner, id_other = self.register(owner, "a@x.com"), self.register(other, "b@x.com")
        self.add_page(id_other, "12345678")      # صفحة يملكها مستخدم آخر

        async def fake_get(client, path, **params):
            if path == "me":
                return {"id": "10", "name": "Owner"}
            if path == "me/accounts":
                return {"data": [
                    {"id": "99999999", "name": "Mine", "access_token": "t1"},
                    {"id": "12345678", "name": "Theirs", "access_token": "t2"},
                ]}
            return {"access_token": "user"}

        async def fake_post(client, path, *, data):
            return {"success": True}

        state = auth._make_state(id_owner)
        owner.cookies.set("hb_oauth", state.split(".")[1])
        with patch.object(auth, "_graph_get", fake_get), patch.object(auth, "_graph_post", fake_post):
            res = owner.get(f"/auth/facebook/callback?code=c&state={state}")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual([p["page_id"] for p in res.json()["pages"]], ["99999999"])
        self.assertEqual(len(res.json()["subscription_errors"]), 1)
        self.assertEqual([p["page_id"] for p in owner.get("/auth/facebook/status").json()["pages"]], ["99999999"])
        self.assertEqual([p["page_id"] for p in other.get("/auth/facebook/status").json()["pages"]], ["12345678"])

    def test_delete_page_only_own(self):
        a, b = self.client(), self.client()
        id_a, id_b = self.register(a, "a@x.com"), self.register(b, "b@x.com")
        self.add_page(id_a, "11111")
        self.assertEqual(b.delete("/auth/facebook/pages/11111").status_code, 404)   # صفحة غيره
        self.assertEqual(self.client().delete("/auth/facebook/pages/11111").status_code, 401)
        self.assertEqual(len(a.get("/auth/facebook/status").json()["pages"]), 1)
        res = a.delete("/auth/facebook/pages/11111")
        self.assertEqual(res.json()["result"], "deleted")
        self.assertEqual(a.get("/auth/facebook/status").json()["pages"], [])
        with self.Session() as db:
            self.assertEqual(db.query(models.SocialChannel).count(), 0)

    def test_delete_page_with_deliveries_is_scrubbed_not_deleted(self):
        a = self.client()
        id_a = self.register(a, "a@x.com")
        self.add_page(id_a, "11111")
        with self.Session() as db:
            channel = db.query(models.SocialChannel).one()
            channel.page_access_token, channel.owner_fbid = "tok", "10"
            db.add(models.Factory(name="f", phone_number="1"))
            db.add(models.Post(factory_id=1, media_type="text"))
            db.flush()
            db.add(models.PostDelivery(post_id=1, channel_id=channel.id))
            db.commit()
        self.assertEqual(a.delete("/auth/facebook/pages/11111").json()["result"], "detached")
        self.assertEqual(a.get("/auth/facebook/status").json()["pages"], [])
        with self.Session() as db:
            channel = db.query(models.SocialChannel).one()
            self.assertIsNone(channel.page_access_token)
            self.assertIsNone(channel.owner_fbid)
            self.assertIsNone(channel.user_id)
            self.assertFalse(channel.is_active)

    def test_callback_rejects_state_from_another_browser(self):
        c = self.client()
        res = c.get(f"/auth/facebook/callback?code=c&state={auth._make_state()}")
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
