"""callback فيسبوك يعيد توجيه المستخدم إلى الواجهة بدل إرجاع JSON."""
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.routers import auth
from app.services import facebook_services

SERVICE = facebook_services.SERVICES["facebook-reels"]


class CallbackRedirectTests(unittest.TestCase):
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
        self.client = TestClient(app, follow_redirects=False)
        self.patches = [
            patch.object(settings, "META_APP_ID", "1"),
            patch.object(settings, "META_APP_SECRET", "secret"),
            patch.object(settings, "FACEBOOK_SUCCESS_REDIRECT_URL", "https://hadjretbladi.com/"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_success_redirects_to_frontend(self):
        async def fake_get(client, path, **params):
            if path == "me":
                return {"id": "10", "name": "Owner"}
            if path == "me/permissions":
                return {"data": [{"permission": s, "status": "granted"} for s in SERVICE.scopes]}
            if path == "me/accounts":
                return {"data": [{"id": "12345678", "name": "Page", "access_token": "tok"}]}
            return {"access_token": "user"}

        with patch.object(auth, "_graph_get", fake_get):
            state = auth._make_state(service=SERVICE.key)
            self.client.cookies.set("hb_oauth", state.split(".")[1])
            res = self.client.get(f"/auth/facebook/callback?code=c&state={state}")
        self.assertEqual(res.status_code, 303)
        self.assertIn("hb_session=", res.headers.get("set-cookie", ""))
        self.assertEqual(
            res.headers["location"], "https://hadjretbladi.com/?facebook=connected&service=facebook-reels"
        )

    def test_login_without_service_links_no_page(self):
        async def fake_get(client, path, **params):
            if path == "me":
                return {"id": "10", "name": "Owner"}
            if path == "oauth/access_token":
                return {"access_token": "user"}
            raise AssertionError(f"طلب غير متوقع بلا خدمة: {path}")

        with patch.object(auth, "_graph_get", fake_get):
            state = auth._make_state()
            self.client.cookies.set("hb_oauth", state.split(".")[1])
            res = self.client.get(f"/auth/facebook/callback?code=c&state={state}")
        self.assertEqual(res.headers["location"], "https://hadjretbladi.com/?facebook=connected")
        self.assertIn("hb_session=", res.headers.get("set-cookie", ""))

    def test_denied_redirects_with_reason(self):
        res = self.client.get("/auth/facebook/callback?error=access_denied")
        self.assertEqual(res.status_code, 303)
        self.assertIn("facebook=error&reason=access_denied", res.headers["location"])

    def test_json_fallback_when_no_frontend_url(self):
        with patch.object(settings, "FACEBOOK_SUCCESS_REDIRECT_URL", ""):
            res = self.client.get("/auth/facebook/callback?error=access_denied")
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
