"""موقع الوسائط حسب المصنع: مصنع صاحب المشروع يبقى بمساراته وروابطه القديمة حرفياً، وغيره في FACTORY_MEDIA_ROOT.
بلا قاعدة بيانات وبلا FastAPI."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PIL import Image

from app.core.config import settings
from app.services import media, media_scan, video_maker

OWNER, OTHER = 4, 9


def jpeg_bytes() -> bytes:
    from io import BytesIO
    out = BytesIO()
    Image.new("RGB", (40, 40), "red").save(out, format="JPEG")
    return out.getvalue()


class FactoryMediaLocationTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.products = base / "products"
        self.factory_root = base / "factory"
        self.products.mkdir()
        self.factory_root.mkdir()
        for name, value in (
            ("MEDIA_ROOT", self.products), ("FACTORY_MEDIA_ROOT", self.factory_root),
            ("MEDIA_BACKUP_DIR", base / "backup"), ("DEFAULT_FACTORY_ID", OWNER),
            ("IMAGE_BASE_URL", "https://hadjretbladi.com"), ("BASE_URL", "https://api.hadjretbladi.com"),
        ):
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # --- صاحب المشروع: لا شيء يتغير ---
    def test_owner_paths_and_urls_are_unchanged(self):
        for fid in (OWNER, None):
            self.assertEqual(media.product_dir("B001", fid), (self.products / "B" / "B001").resolve())
            self.assertEqual(media.public_image_url("B001", ".jpg", 2, fid), "https://hadjretbladi.com/B001/2.jpg")
        self.assertEqual(media.public_image_url("B001", ".jpg"), "https://hadjretbladi.com/B001/1.jpg")

    def test_without_default_factory_everyone_gets_legacy_behaviour(self):
        with mock.patch.object(settings, "DEFAULT_FACTORY_ID", None):
            self.assertTrue(media.is_owner_factory(OTHER))
            self.assertEqual(media.public_image_url("B001", ".jpg", factory_id=OTHER), "https://hadjretbladi.com/B001/1.jpg")

    # --- مصنع آخر ---
    def test_other_factory_paths_and_urls(self):
        self.assertEqual(media.product_dir("ST012", OTHER), (self.factory_root / "9" / "ST" / "ST012").resolve())
        self.assertEqual(
            media.public_image_url("ST012", ".png", 3, OTHER),
            "https://api.hadjretbladi.com/api/media/factories/9/ST012/3.png",
        )
        self.assertEqual(
            media.public_media_url("ST012", "2.mp4", OTHER),
            "https://api.hadjretbladi.com/api/media/factories/9/ST012/2.mp4",
        )

    def test_invalid_sku_and_traversal_rejected_for_both(self):
        for fid in (OWNER, OTHER):
            for bad in ("../x", "b001", "B001/..", ""):
                with self.assertRaises(media.InvalidSkuError):
                    media.product_dir(bad, fid)

    def test_save_main_image_goes_to_the_right_root_and_factories_are_isolated(self):
        media.save_main_image("B001", jpeg_bytes(), OWNER)
        media.save_main_image("ST012", jpeg_bytes(), OTHER)
        self.assertTrue((self.products / "B" / "B001" / "1.jpg").is_file())
        self.assertTrue((self.factory_root / "9" / "ST" / "ST012" / "1.jpg").is_file())
        self.assertFalse((self.products / "ST").exists())                       # لا تسرّب لمجلد صاحب المشروع
        self.assertFalse((self.factory_root / "9" / "B").exists())
        self.assertTrue(media.has_media("ST012", OTHER))
        self.assertFalse(media.has_media("ST012", OWNER))
        self.assertEqual(media.existing_image_ext("ST012", factory_id=OTHER), ".jpg")
        self.assertIsNone(media.existing_image_ext("ST012", factory_id=OWNER))

    def test_scan_and_records_for_other_factory(self):
        media.save_main_image("ST012", jpeg_bytes(), OTHER)
        scan = media_scan.scan_media_root(media.media_root_for(OTHER))
        self.assertEqual(list(scan.images), ["ST012"])
        build = media_scan.build_records(scan, {"ST012": {"title": "حجر", "price": "100"}}, OTHER)
        self.assertEqual(
            build.records[0]["primary_media_url"],
            "https://api.hadjretbladi.com/api/media/factories/9/ST012/1.jpg",
        )
        # نفس الفحص لصاحب المشروع لا يرى منتجات المصنع الآخر
        self.assertEqual(media_scan.scan_media_root(media.media_root_for(OWNER)).images, {})

    def test_library_lives_in_its_own_folder_apart_from_products(self):
        try:
            from app.services import media_library
        except ImportError:
            self.skipTest("حزم القاعدة (sqlalchemy) غير مثبتة في هذه البيئة")
        folder = media_library.library_dir(OTHER)
        self.assertEqual(folder, self.factory_root / "9" / "library")
        folder.mkdir(parents=True)
        name = "a" * 32 + ".jpg"
        (folder / name).write_bytes(b"library file")
        media.save_main_image("ST012", jpeg_bytes(), OTHER)
        self.assertEqual(list(media_scan.scan_media_root(media.media_root_for(OTHER)).images), ["ST012"])
        self.assertEqual(media_library.factory_media_file_path(OTHER, name), (folder / name).resolve())
        self.assertIsNone(media_library.factory_media_file_path(OTHER, "../" + name))
        # مكتبة مصنع صاحب المشروع أيضاً في FACTORY_MEDIA_ROOT وليس MEDIA_ROOT
        self.assertEqual(media_library.library_dir(OWNER), self.factory_root / "4" / "library")

    def test_videos_follow_the_factory(self):
        if not video_maker.shutil.which("ffmpeg"):
            self.skipTest("ffmpeg غير مثبت")
        media.save_main_image("ST012", jpeg_bytes(), OTHER)
        media.save_main_image("B001", jpeg_bytes(), OWNER)
        other = video_maker.create_product_video("ST012", duration=3, factory_id=OTHER)
        owner = video_maker.create_product_video("B001", duration=3, factory_id=OWNER)
        self.assertEqual(other.path, (self.factory_root / "9" / "ST" / "ST012" / "1.mp4").resolve())
        self.assertEqual(other.url, "https://api.hadjretbladi.com/api/media/factories/9/ST012/1.mp4")
        self.assertEqual(owner.url, "https://hadjretbladi.com/B001/1.mp4")
        self.assertEqual(video_maker.video_indexes("ST012", OTHER), [1])
        self.assertEqual(video_maker.video_indexes("ST012", OWNER), [])


if __name__ == "__main__":
    unittest.main()
