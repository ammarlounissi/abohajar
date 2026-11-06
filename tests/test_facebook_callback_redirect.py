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
            if path == "me/accounts":
                return {"data": [{"id": "12345678", "name": "Page", "access_token": "tok"}]}
            return {"access_token": "user"}

        async def fake_post(client, path, *, data):
            return {"success": True}

        with patch.object(auth, "_graph_get", fake_get), patch.object(auth, "_graph_post", fake_post):
            res = self.client.get(f"/auth/facebook/callback?code=c&state={auth._make_state()}")
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers["location"], "https://hadjretbladi.com/?facebook=connected&pages=1&errors=0")

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
