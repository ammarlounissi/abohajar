"""اختبارات app/services/media_scan.py (بلا قاعدة بيانات).  python -m unittest discover -s tests -v"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.config import settings
from app.services import media_scan


def touch(path: Path, data: bytes = b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class ScanTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        p = mock.patch.object(settings, "IMAGE_BASE_URL", "https://hadjretbladi.com")
        p.start()
        self.addCleanup(p.stop)

    def test_scan_counts_images_and_sorts_numerically(self):
        for name in ("1.jpg", "2.webp", "10.png", "3.jpeg"):
            touch(self.root / "B" / "B001" / name)
        touch(self.root / "B" / "B001" / "video.mp4")        # يُتجاهل
        touch(self.root / "B" / "B001" / "7.mp4")            # فيديو برقم، يُتجاهل
        res = media_scan.scan_media_root(self.root)
        self.assertEqual(res.images["B001"], [(1, ".jpg"), (2, ".webp"), (3, ".jpeg"), (10, ".png")])
        # الترقيم فيه فجوة فيُنبَّه إليه، لكن لا يمنع الاستيراد
        self.assertTrue(any("غير متسلسل" in w for w in res.warnings))

    def test_empty_and_missing_folders(self):
        (self.root / "B" / "B002").mkdir(parents=True)
        touch(self.root / "B" / "B003" / "notes.txt")
        touch(self.root / "B" / "B004" / ".DS_Store")
        res = media_scan.scan_media_root(self.root)
        self.assertEqual(sorted(res.empty), ["B002", "B003", "B004"])
        self.assertEqual(res.images, {})

    def test_ignores_non_category_entries(self):
        touch(self.root / "products.csv")
        touch(self.root / ".stfolder" / "x")
        touch(self.root / "b" / "B001" / "1.jpg")             # فئة بحروف صغيرة
        touch(self.root / "123" / "B001" / "1.jpg")
        self.assertEqual(media_scan.scan_media_root(self.root).images, {})

    def test_misplaced_or_invalid_sku_folders_warned(self):
        touch(self.root / "B" / "F001" / "1.jpg")             # SKU في فئة خطأ
        touch(self.root / "B" / "b002" / "1.jpg")
        touch(self.root / "B" / "STONE-01" / "1.jpg")
        res = media_scan.scan_media_root(self.root)
        self.assertEqual(res.images, {})
        self.assertEqual(len(res.warnings), 3)

    def test_uppercase_extension_kept_as_is_in_url(self):
        touch(self.root / "B" / "B001" / "1.JPG")
        touch(self.root / "B" / "B001" / "2.jpg")
        res = media_scan.scan_media_root(self.root)
        self.assertEqual(res.images["B001"], [(1, ".JPG"), (2, ".jpg")])
        self.assertEqual(res.warnings, [])

    def test_duplicate_index_with_two_files_warned_first_wins(self):
        touch(self.root / "B" / "B001" / "1.jpg")
        touch(self.root / "B" / "B001" / "1.png")
        res = media_scan.scan_media_root(self.root)
        self.assertEqual(res.images["B001"], [(1, ".jpg")])
        self.assertTrue(any("أكثر من ملف" in w for w in res.warnings))

    def test_sync_conflict_files_ignored(self):
        touch(self.root / "B" / "B001" / "1.jpg")
        touch(self.root / "B" / "B001" / "1.sync-conflict-20261008-101010-ABCDEFG.jpg")
        res = media_scan.scan_media_root(self.root)
        self.assertEqual(res.images["B001"], [(1, ".jpg")])
        self.assertTrue(any("تعارض" in w for w in res.warnings))

    def test_arabic_indic_digit_names_are_not_numbers(self):
        touch(self.root / "B" / "B001" / "١.jpg")
        self.assertEqual(media_scan.scan_media_root(self.root).images, {})


class CsvAndBuildTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        p = mock.patch.object(settings, "IMAGE_BASE_URL", "https://hadjretbladi.com")
        p.start()
        self.addCleanup(p.stop)

    def _csv(self, text: str, encoding="utf-8-sig") -> Path:
        path = self.root / "p.csv"
        path.write_bytes(text.encode(encoding))
        return path

    def test_reads_bom_semicolon_and_decimal_comma(self):
        rows, warns = media_scan.read_csv_rows(self._csv("SKU;Title;Price\nb001;حجر بيج;3200,5\n"))
        self.assertEqual(rows, {"B001": {"title": "حجر بيج", "price": "3200.5"}})
        self.assertEqual(warns, [])

    def test_duplicate_sku_warns_and_last_wins(self):
        rows, warns = media_scan.read_csv_rows(self._csv("sku,title,price\nB001,a,1\nB001,b,2\n"))
        self.assertEqual(rows["B001"]["title"], "b")
        self.assertEqual(len(warns), 1)

    def test_missing_column_raises(self):
        with self.assertRaises(ValueError):
            media_scan.read_csv_rows(self._csv("sku,title\nB001,a\n"))

    def test_build_records_urls_and_gaps(self):
        scan = media_scan.ScanResult(
            images={"B001": [(1, ".jpg"), (2, ".png"), (3, ".JPG")], "B002": [(1, ".jpg")], "B003": [(1, ".jpg")]},
            empty=["B004"],
        )
        rows = {
            "B001": {"title": "أ", "price": "10"},
            "B002": {"title": "ب", "price": "20"},
            "B004": {"title": "د", "price": "40"},   # مجلده فارغ
            "B005": {"title": "هـ", "price": "50"},  # بلا مجلد
        }
        build = media_scan.build_records(scan, rows)
        base = "https://hadjretbladi.com"
        self.assertEqual(build.records[0], {
            "sku": "B001", "title": "أ", "price": "10",
            "primary_media_url": f"{base}/B001/1.jpg",
            "additional_media_urls": [f"{base}/B001/2.png", f"{base}/B001/3.JPG"],
        })
        self.assertEqual(build.records[1]["additional_media_urls"], [])
        self.assertEqual(build.images_without_csv, ["B003"])
        self.assertEqual(build.csv_without_images, ["B004", "B005"])


if __name__ == "__main__":
    unittest.main()
