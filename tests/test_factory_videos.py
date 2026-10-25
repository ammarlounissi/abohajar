"""مسارات توليد فيديو المصنع ومصادر الصور ومواقع حفظ النتيجة."""
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
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import require_admin
from app.routers import factories as factories_router
from app.services import errors, media, media_library


class FactoryVideoRoutesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.products_root = root / "products"
        self.factory_media_root = root / "factory"
        self.audio_root = root / "audio"
        self.audio_root.mkdir()
        (self.audio_root / "a.mp3").write_bytes(b"audio")
        (self.audio_root / "z.wav").write_bytes(b"audio")
        for name, value in (
            ("MEDIA_ROOT", self.products_root),
            ("FACTORY_MEDIA_ROOT", self.factory_media_root),
            ("DEFAULT_FACTORY_ID", 99),
            ("BASE_URL", "https://api.example.test"),
            ("IMAGE_BASE_URL", "https://images.example.test"),
            ("AUDIO_ROOT", self.audio_root),
        ):
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add_all([
                models.Factory(id=1, name="مصنع أول", phone_number="213550000000"),
                models.Factory(id=2, name="مصنع ثان", phone_number="213550000001"),
            ])
            db.add_all([
                models.Product(
                    sku="ST1", category_code="STONE", title="منتج أول", price=10,
                    primary_media_url="https://example.test/ST1/1.jpg", factory_id=1,
                ),
                models.Product(
                    sku="ST2", category_code="STONE", title="منتج ثان", price=10,
                    primary_media_url="https://example.test/ST2/1.jpg", factory_id=2,
                ),
            ])
            db.commit()

        self.product_dir = media.product_dir("ST1", 1)
        self.product_dir.mkdir(parents=True)
        (self.product_dir / "1.jpg").write_bytes(b"image")

        app = FastAPI()
        app.include_router(factories_router.router)

        @app.exception_handler(errors.ServiceError)
        async def handle_service_error(request: Request, exc: errors.ServiceError):
            status_code = 404 if isinstance(exc, errors.NotFoundError) else 422
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
        self.ffmpeg = mock.patch(
            "app.services.video_maker.ffmpeg_tools.find_tool", return_value="/usr/bin/ffmpeg",
        )
        self.ffmpeg.start()
        self.addCleanup(self.ffmpeg.stop)
        self.render = mock.patch("app.services.video_maker._run_ffmpeg", side_effect=self._write_video)
        self.render.start()
        self.addCleanup(self.render.stop)
        self.addCleanup(engine.dispose)

    @staticmethod
    def _write_video(_ffmpeg, _images, _duration, _fit, _crf, output, _audio):
        output.write_bytes(b"generated-video")

    def add_library_image(self, factory_id=1):
        filename = "a" * 32 + ".jpg"
        directory = media_library.library_dir(factory_id)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / filename).write_bytes(b"image")
        with self.Session() as db:
            db.add(models.Media(
                factory_id=factory_id,
                media_type="image",
                url=f"https://api.example.test/api/media/factories/{factory_id}/{filename}",
                description="صورة",
            ))
            db.commit()

    def add_external_library_image(self, url="https://cdn.example.test/factory/image.jpg"):
        with self.Session() as db:
            db.add(models.Media(
                factory_id=1,
                media_type="image",
                url=url,
                description="صورة خارجية",
            ))
            db.commit()

    def test_options_are_factory_scoped_and_include_default_audio(self):
        self.add_library_image()

        response = self.client.get("/api/factories/1/videos/options")

        self.assertEqual(response.status_code, 200)
        options = response.json()
        self.assertEqual(options["factory_id"], 1)
        self.assertEqual([item["sku"] for item in options["products"]], ["ST1"])
        self.assertEqual(options["audio_files"], ["a.mp3", "z.wav"])
        self.assertEqual(options["default_audio"], "a.mp3")
        self.assertEqual(options["library_images_count"], 1)
        self.assertEqual(options["max_duration"], 60)

    def test_specific_product_video_is_saved_in_that_product_folder(self):
        response = self.client.post("/api/factories/1/videos/generate", json={
            "source": "specific_product", "sku": "st1", "duration": 20,
        })

        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertEqual(result["sku"], "ST1")
        self.assertEqual(result["audio"], "a.mp3")
        self.assertEqual(result["images_used"], 1)
        self.assertEqual(
            result["video_url"],
            "https://api.example.test/api/media/factories/1/ST1/1.mp4",
        )
        self.assertTrue((self.product_dir / "1.mp4").is_file())
        with self.Session() as db:
            self.assertEqual(db.query(models.Product).filter_by(sku="ST1").one().video_url, result["video_url"])
            self.assertEqual(db.query(models.Media).count(), 0)

    def test_library_video_is_saved_and_registered_in_factory_library(self):
        self.add_library_image()

        response = self.client.post("/api/factories/1/videos/generate", json={
            "source": "library", "duration": 60,
        })

        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertIsNone(result["sku"])
        self.assertEqual(result["duration"], 60)
        self.assertEqual(result["audio"], "a.mp3")
        saved_name = result["video_url"].rsplit("/", 1)[-1]
        saved_file = media_library.factory_media_file_path(1, saved_name)
        self.assertIsNotNone(saved_file)
        self.assertTrue(saved_file.is_file())
        with self.Session() as db:
            video = db.query(models.Media).filter_by(media_type="video").one()
            self.assertEqual(video.url, result["video_url"])
            self.assertEqual(video.factory_id, 1)
            self.assertEqual(db.query(models.Product).filter_by(sku="ST1").one().video_url, None)

    def test_library_source_uses_registered_external_image_links(self):
        image_url = "https://cdn.example.test/images/random.jpg"
        self.add_external_library_image(image_url)

        def download(url, destination):
            self.assertEqual(url, image_url)
            destination.write_bytes(b"downloaded-and-validated-jpeg")

        with mock.patch("app.services.factory_videos._download_library_image", side_effect=download):
            response = self.client.post("/api/factories/1/videos/generate", json={
                "source": "library", "duration": 10,
            })

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["images_used"], 1)

    def test_options_count_external_images_registered_in_library(self):
        self.add_external_library_image()

        response = self.client.get("/api/factories/1/videos/options")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["library_images_count"], 1)

    def test_random_product_stays_within_factory(self):
        response = self.client.post("/api/factories/1/videos/generate", json={
            "source": "random_product",
        })

        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["sku"], "ST1")

    def test_product_from_another_factory_and_invalid_request_are_rejected(self):
        wrong_factory = self.client.post("/api/factories/1/videos/generate", json={
            "source": "specific_product", "sku": "ST2",
        })
        missing_sku = self.client.post("/api/factories/1/videos/generate", json={
            "source": "specific_product",
        })
        too_long = self.client.post("/api/factories/1/videos/generate", json={
            "source": "random_product", "duration": 61,
        })

        self.assertEqual(wrong_factory.status_code, 404)
        self.assertEqual(missing_sku.status_code, 422)
        self.assertEqual(too_long.status_code, 422)
