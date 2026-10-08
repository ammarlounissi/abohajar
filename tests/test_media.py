"""اختبارات app/services/media.py. التشغيل:  python -m unittest discover -s tests -v"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.config import settings
from app.services import media

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 32


class MediaTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        (base / "products").mkdir()
        self.root = base / "products"
        self.backup = base / "backup"
        patches = [
            mock.patch.object(settings, "MEDIA_ROOT", self.root),
            mock.patch.object(settings, "MEDIA_BACKUP_DIR", self.backup),
            mock.patch.object(settings, "IMAGE_BASE_URL", "https://hadjretbladi.com"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    # --- SKU والمسارات ---
    def test_category_is_leading_letters(self):
        self.assertEqual(media.category_of("B001"), "B")
        self.assertEqual(media.category_of("PTK012"), "PTK")

    def test_invalid_skus_rejected(self):
        for bad in ["", "b001", "B", "001", "B-001", "STONE-GLM-01", "../B001",
                    "B001/../../x", "B001\n", "B 001", "B001.jpg", None]:
            with self.subTest(sku=bad):
                self.assertFalse(media.is_valid_media_sku(bad))
                with self.assertRaises(media.InvalidSkuError):
                    media.product_dir(bad)

    def test_product_dir_layout(self):
        self.assertEqual(media.product_dir("B001"), self.root.resolve() / "B" / "B001")

    def test_public_url_is_short_with_real_extension(self):
        self.assertEqual(media.public_image_url("B001", ".jpg"), "https://hadjretbladi.com/B001/1.jpg")
        self.assertEqual(media.public_image_url("B010", ".png", 22), "https://hadjretbladi.com/B010/22.png")

    def test_existing_image_ext(self):
        self.assertIsNone(media.existing_image_ext("B001"))
        media.save_main_image("B001", PNG)
        self.assertEqual(media.existing_image_ext("B001"), ".png")
        self.assertIsNone(media.existing_image_ext("B001", 2))

    # --- الكشف عن نوع الصورة ---
    def test_detect_extension_from_content(self):
        self.assertEqual(media.detect_image_extension(JPEG), ".jpg")
        self.assertEqual(media.detect_image_extension(PNG), ".png")
        self.assertEqual(media.detect_image_extension(WEBP), ".webp")
        self.assertIsNone(media.detect_image_extension(b"<html>not an image</html>"))

    # --- الحفظ ---
    def test_save_creates_folder_and_file(self):
        self.assertFalse(media.has_media("B001"))
        replaced = media.save_main_image("B001", JPEG)
        self.assertFalse(replaced)
        self.assertEqual((self.root / "B" / "B001" / "1.jpg").read_bytes(), JPEG)
        self.assertTrue(media.has_media("B001"))

    def test_non_image_rejected_and_nothing_created(self):
        with self.assertRaises(ValueError):
            media.save_main_image("B001", b"hello")
        self.assertFalse((self.root / "B" / "B001").exists())

    def test_replace_archives_old_image_even_if_extension_changes(self):
        media.save_main_image("B001", JPEG)
        replaced = media.save_main_image("B001", PNG)
        self.assertTrue(replaced)
        folder = self.root / "B" / "B001"
        self.assertFalse((folder / "1.jpg").exists())
        self.assertEqual((folder / "1.png").read_bytes(), PNG)
        archived = list((self.backup / "B001").iterdir())
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), JPEG)

    def test_replace_archives_uppercase_extension_too(self):
        folder = self.root / "B" / "B001"
        folder.mkdir(parents=True)
        (folder / "1.JPG").write_bytes(JPEG)
        media.save_main_image("B001", PNG)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["1.png"])
        self.assertEqual(len(list((self.backup / "B001").iterdir())), 1)

    def test_replace_keeps_other_files(self):
        media.save_main_image("B001", JPEG)
        folder = self.root / "B" / "B001"
        (folder / "2.jpg").write_bytes(b"second")
        (folder / "3.mp4").write_bytes(b"video")
        media.save_main_image("B001", WEBP)
        self.assertEqual((folder / "2.jpg").read_bytes(), b"second")
        self.assertEqual((folder / "3.mp4").read_bytes(), b"video")

    def test_no_temp_files_left(self):
        media.save_main_image("B001", JPEG)
        names = [p.name for p in (self.root / "B" / "B001").iterdir()]
        self.assertEqual(names, ["1.jpg"])

    # --- has_media ---
    def test_empty_or_hidden_only_folder_is_not_media(self):
        folder = self.root / "F" / "F001"
        folder.mkdir(parents=True)
        self.assertFalse(media.has_media("F001"))
        (folder / ".DS_Store").write_bytes(b"x")
        (folder / "x.tmp").write_bytes(b"x")
        self.assertFalse(media.has_media("F001"))
        (folder / "1.jpg").write_bytes(JPEG)
        self.assertTrue(media.has_media("F001"))


if __name__ == "__main__":
    unittest.main()
