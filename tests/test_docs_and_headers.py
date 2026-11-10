"""التوثيق (/docs, /redoc, /openapi.json) محجوب خلف Basic Auth بمفتاح الإدارة، وترويسات الأمان على كل الردود."""
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app

_KEY = "test-admin-key"
_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


class DocsAccessTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(settings, "ADMIN_API_KEY", _KEY)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app)   # بلا with: لا يُشغَّل lifespan ولا settings.validate()

    def test_anonymous_is_challenged(self):
        for path in _DOC_PATHS:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertTrue(response.headers["WWW-Authenticate"].startswith("Basic"), path)

    def test_wrong_password_rejected(self):
        for path in _DOC_PATHS:
            self.assertEqual(self.client.get(path, auth=("admin", "wrong")).status_code, 401, path)

    def test_admin_key_as_password_opens_docs(self):
        for path in _DOC_PATHS:
            self.assertEqual(self.client.get(path, auth=("anything", _KEY)).status_code, 200, path)

    def test_schema_lists_api_but_not_doc_routes(self):
        paths = self.client.get("/openapi.json", auth=("admin", _KEY)).json()["paths"]
        self.assertIn("/api/products/", paths)
        for path in _DOC_PATHS:
            self.assertNotIn(path, paths)

    def test_empty_admin_key_never_opens_docs(self):
        with mock.patch.object(settings, "ADMIN_API_KEY", ""):
            self.assertEqual(self.client.get("/docs", auth=("admin", "")).status_code, 401)

    def test_api_key_header_alone_is_not_enough(self):
        self.assertEqual(self.client.get("/docs", headers={"X-API-Key": _KEY}).status_code, 401)


class SecurityHeadersTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def assert_security_headers(self, response):
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertIn("max-age=", response.headers["Strict-Transport-Security"])

    def test_on_success(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assert_security_headers(response)

    def test_on_unauthorized_and_not_found(self):
        self.assert_security_headers(self.client.get("/api/factories/"))
        self.assert_security_headers(self.client.get("/no-such-path"))

    def test_on_cors_preflight(self):
        origin = settings.ALLOWED_ORIGINS[0]
        response = self.client.options(
            "/auth/me", headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
        self.assert_security_headers(response)


if __name__ == "__main__":
    unittest.main()
