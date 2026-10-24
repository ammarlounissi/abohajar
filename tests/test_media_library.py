"""اختبارات مكتبة الوسائط التابعة للمصانع."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.database import Base, get_db
from app.core.config import settings
from app.core.security import require_admin
import app.services.social  # noqa: F401
from app.routers import factories as factories_router, public_media
from app.services import errors


class FactoryMediaLibraryTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add_all([
                models.Factory(id=1, name="مصنع أول", phone_number="213550000000"),
                models.Factory(id=2, name="مصنع ثان", phone_number="213550000001"),
            ])
            db.commit()

        app = FastAPI()
        app.include_router(factories_router.router)
        app.include_router(public_media.router)

        @app.exception_handler(errors.ServiceError)
        async def handle_service_error(request: Request, exc: errors.ServiceError):
            if isinstance(exc, errors.NotFoundError):
                status_code = 404
            elif isinstance(exc, errors.InvalidInputError):
                status_code = 422
            else:
                status_code = 400
            return JSONResponse(status_code=status_code, content={"detail": str(exc)})

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[require_admin] = lambda: None
        self.client = TestClient(app)
        self.addCleanup(engine.dispose)

    def test_media_library_is_scoped_and_filterable_by_factory_and_type(self):
        image = self.client.post("/api/factories/1/media", json={
            "media_type": "image", "url": "https://example.test/image.jpg", "description": "صورة المنتج",
        })
        video = self.client.post("/api/factories/1/media", json={
            "media_type": "video", "url": "https://example.test/video.mp4",
        })
        other_factory = self.client.post("/api/factories/2/media", json={
            "media_type": "image", "url": "https://example.test/other.jpg",
        })

        self.assertEqual((image.status_code, video.status_code, other_factory.status_code), (201, 201, 201))
        self.assertEqual(image.json()["factory_id"], 1)
        self.assertEqual(
            [item["media_type"] for item in self.client.get("/api/factories/1/media").json()],
            ["video", "image"],
        )
        images = self.client.get("/api/factories/1/media?media_type=image").json()
        self.assertEqual([item["id"] for item in images], [image.json()["id"]])
        self.assertEqual(
            [item["factory_id"] for item in self.client.get("/api/factories/2/media").json()],
            [2],
        )

    def test_create_validates_media_type_and_url(self):
        self.assertEqual(self.client.post("/api/factories/1/media", json={
            "media_type": "audio", "url": "https://example.test/audio.mp3",
        }).status_code, 422)
        self.assertEqual(self.client.post("/api/factories/1/media", json={
            "media_type": "image", "url": "not-a-url",
        }).status_code, 422)
        self.assertEqual(self.client.get("/api/factories/1/media?media_type=audio").status_code, 422)

    def test_delete_only_removes_media_from_its_own_factory(self):
        created = self.client.post("/api/factories/1/media", json={
            "media_type": "image", "url": "https://example.test/image.jpg",
        })
        media_id = created.json()["id"]

        self.assertEqual(self.client.delete(f"/api/factories/2/media/{media_id}").status_code, 404)
        self.assertEqual(self.client.get("/api/factories/1/media").json()[0]["id"], media_id)
        self.assertEqual(self.client.delete(f"/api/factories/1/media/{media_id}").status_code, 200)
        self.assertEqual(self.client.get("/api/factories/1/media").json(), [])

    def test_upload_saves_file_in_factory_directory_and_returns_public_url(self):
        with TemporaryDirectory() as temp_dir, \
                mock.patch.object(settings, "FACTORY_MEDIA_ROOT", Path(temp_dir)), \
                mock.patch.object(settings, "BASE_URL", "https://api.example.test"):
            uploaded = self.client.post(
                "/api/factories/1/media/upload",
                files={"file": ("sample.jpg", b"image bytes", "image/jpeg")},
                data={"description": "صورة المصنع"},
            )

            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            media = uploaded.json()
            self.assertEqual(media["media_type"], "image")
            self.assertEqual(media["description"], "صورة المصنع")
            self.assertTrue(media["url"].startswith("https://api.example.test/api/media/factories/1/"))
            stored_path = Path(temp_dir) / "1" / "library" / media["url"].rsplit("/", 1)[-1]
            self.assertEqual(stored_path.read_bytes(), b"image bytes")

            served = self.client.get(media["url"].replace("https://api.example.test", ""))
            self.assertEqual(served.status_code, 200)
            self.assertEqual(served.content, b"image bytes")
            self.assertEqual(served.headers["content-type"], "image/jpeg")
            self.assertEqual(served.headers["x-content-type-options"], "nosniff")

            deleted = self.client.delete(f"/api/factories/1/media/{media['id']}")
            self.assertEqual(deleted.status_code, 200)
            self.assertFalse(stored_path.exists())
            self.assertEqual(
                self.client.get(media["url"].replace("https://api.example.test", "")).status_code,
                404,
            )

    def test_upload_rejects_unsupported_and_mismatched_file_types(self):
        with TemporaryDirectory() as temp_dir, \
                mock.patch.object(settings, "FACTORY_MEDIA_ROOT", Path(temp_dir)):
            unsupported = self.client.post(
                "/api/factories/1/media/upload",
                files={"file": ("payload.svgz", b"<svg/>", "image/svg+xml")},
            )
            mismatch = self.client.post(
                "/api/factories/1/media/upload",
                files={"file": ("payload.mp4", b"not a video", "image/jpeg")},
            )
            self.assertEqual(unsupported.status_code, 422)
            self.assertEqual(mismatch.status_code, 422)
            self.assertEqual(list(Path(temp_dir).rglob("*")), [])

    def test_upload_and_serve_ico_and_svg(self):
        files = (
            ("logo.ico", b"icon data", "image/vnd.microsoft.icon", "image/x-icon"),
            ("logo.svg", b"<svg></svg>", "image/svg+xml", "image/svg+xml"),
        )
        with TemporaryDirectory() as temp_dir, \
                mock.patch.object(settings, "FACTORY_MEDIA_ROOT", Path(temp_dir)), \
                mock.patch.object(settings, "BASE_URL", "https://api.example.test"):
            for name, content, upload_type, response_type in files:
                with self.subTest(extension=Path(name).suffix):
                    uploaded = self.client.post(
                        "/api/factories/1/media/upload",
                        files={"file": (name, content, upload_type)},
                    )

                    self.assertEqual(uploaded.status_code, 201, uploaded.text)
                    media = uploaded.json()
                    self.assertEqual(media["media_type"], "image")
                    served = self.client.get(
                        media["url"].replace("https://api.example.test", "")
                    )
                    self.assertEqual(served.status_code, 200)
                    self.assertEqual(served.content, content)
                    self.assertEqual(served.headers["content-type"], response_type)
                    if name.endswith(".svg"):
                        self.assertIn("attachment", served.headers["content-disposition"])
                    self.assertEqual(
                        self.client.delete(f"/api/factories/1/media/{media['id']}").status_code,
                        200,
                    )

    def test_upload_enforces_file_size_limit_and_removes_partial_file(self):
        with TemporaryDirectory() as temp_dir, \
                mock.patch.object(settings, "FACTORY_MEDIA_ROOT", Path(temp_dir)), \
                mock.patch("app.services.media_library.MAX_UPLOAD_BYTES", 4):
            response = self.client.post(
                "/api/factories/1/media/upload",
                files={"file": ("sample.png", b"12345", "image/png")},
            )

            self.assertEqual(response.status_code, 422)
            self.assertEqual([path for path in Path(temp_dir).rglob("*") if path.is_file()], [])

    def test_upload_retries_on_filename_collision_without_changing_existing_file(self):
        existing_filename = "a" * 32
        replacement_filename = "b" * 32
        with TemporaryDirectory() as temp_dir, \
                mock.patch.object(settings, "FACTORY_MEDIA_ROOT", Path(temp_dir)), \
                mock.patch.object(settings, "BASE_URL", "https://api.example.test"), \
                mock.patch(
                    "app.services.media_library.uuid.uuid4",
                    side_effect=[mock.Mock(hex=existing_filename), mock.Mock(hex=replacement_filename)],
                ):
            factory_dir = Path(temp_dir) / "1" / "library"
            factory_dir.mkdir(parents=True)
            existing_file = factory_dir / f"{existing_filename}.jpg"
            existing_file.write_bytes(b"existing media")

            uploaded = self.client.post(
                "/api/factories/1/media/upload",
                files={"file": ("sample.jpg", b"new media", "image/jpeg")},
            )

            self.assertEqual(uploaded.status_code, 201, uploaded.text)
            self.assertEqual(existing_file.read_bytes(), b"existing media")
            new_file = factory_dir / f"{replacement_filename}.jpg"
            self.assertEqual(new_file.read_bytes(), b"new media")
            self.assertTrue(uploaded.json()["url"].endswith(new_file.name))

    def test_public_factory_media_route_rejects_invalid_filename(self):
        with TemporaryDirectory() as temp_dir, \
                mock.patch.object(settings, "FACTORY_MEDIA_ROOT", Path(temp_dir)):
            response = self.client.get("/api/media/factories/1/../../secret.jpg")
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
