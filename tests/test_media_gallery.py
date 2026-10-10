"""اختبارات إضافات media.py: عدة صور، الحذف مع إعادة الترقيم، الفيديو، عزل المجلد الفاشل."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.config import settings
from app.services import media

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 16


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        (base / "products").mkdir()
        self.root, self.backup = base / "products", base / "backup"
        for p in (mock.patch.object(settings, "MEDIA_ROOT", self.root),
                  mock.patch.object(settings, "MEDIA_BACKUP_DIR", self.backup),
                  mock.patch.object(settings, "IMAGE_BASE_URL", "https://hadjretbladi.com")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_save_numbered_images_and_urls(self):
        media.save_image("B001", 1, JPEG)
        media.save_image("B001", 2, PNG)
        self.assertEqual(media.list_images("B001"), [(1, ".jpg"), (2, ".png")])
        self.assertEqual(media.image_urls("B001"),
                         ["https://hadjretbladi.com/B001/1.jpg", "https://hadjretbladi.com/B001/2.png"])

    def test_replace_archives_old_even_with_other_extension(self):
        media.save_image("B001", 1, JPEG)
        self.assertTrue(media.save_image("B001", 1, PNG))
        self.assertEqual(media.list_images("B001"), [(1, ".png")])
        self.assertEqual(len(list((self.backup / "B001").iterdir())), 1)

    def test_invalid_content_and_index_rejected(self):
        with self.assertRaises(ValueError):
            media.save_image("B001", 1, b"not an image")
        with self.assertRaises(ValueError):
            media.save_image("B001", 0, JPEG)

    def test_delete_archives_and_renumbers(self):
        for i, data in enumerate([JPEG, PNG, JPEG], start=1):
            media.save_image("B001", i, data)
        self.assertTrue(media.delete_image("B001", 1))
        self.assertEqual(media.list_images("B001"), [(1, ".png"), (2, ".jpg")])
        self.assertEqual(len(list((self.backup / "B001").iterdir())), 1)     # لا حذف نهائي
        self.assertFalse(media.delete_image("B001", 5))

    def test_video_save_replace_remove(self):
        self.assertIsNone(media.video_url("B001"))
        self.assertEqual(media.save_video("B001", MP4), "https://hadjretbladi.com/B001/video.mp4")
        media.save_video("B001", MP4)
        self.assertEqual(len(list((self.backup / "B001").iterdir())), 1)
        self.assertTrue(media.remove_video("B001"))
        self.assertIsNone(media.video_url("B001"))
        self.assertFalse(media.remove_video("B001"))
        with self.assertRaises(ValueError):
            media.save_video("B001", JPEG)

    def test_video_does_not_count_as_image(self):
        media.save_image("B001", 1, JPEG)
        media.save_video("B001", MP4)
        self.assertEqual(media.list_images("B001"), [(1, ".jpg")])

    def test_quarantine_frees_the_sku(self):
        media.save_image("B001", 1, JPEG)
        self.assertTrue(media.has_media("B001"))
        dest = media.quarantine_product_dir("B001")
        self.assertTrue(dest.is_dir())
        self.assertFalse(media.has_media("B001"))
        self.assertIsNone(media.quarantine_product_dir("B001"))


if __name__ == "__main__":
    unittest.main()
