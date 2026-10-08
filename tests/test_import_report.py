import unittest
from pathlib import Path

from app.services.import_report import ImportReport, format_report


class FormatReportTests(unittest.TestCase):
    def make(self, n=40, applied=False):
        return ImportReport(
            csv_path=Path("/data/products.csv"), csv_rows=100, folders_with_images=80, total_images=300,
            empty_folders=3,
            created=[f"B{i:03d}" for i in range(n)], updated=["F001"], unchanged=["M001", "M002"],
            images_without_csv=[f"X{i:03d}" for i in range(n)], applied=applied,
            backup_path=Path("/app/hojrat_bladi.db.bak-1") if applied else None,
        )

    def test_counts_line(self):
        text = format_report(self.make(n=2))
        self.assertIn("جديد: 2", text)
        self.assertIn("تحديث: 1", text)
        self.assertIn("بلا تغيير: 2", text)

    def test_limit_truncates_lists_but_keeps_counts(self):
        text = format_report(self.make(), limit=5)
        self.assertIn("(40)", text)
        self.assertIn("و35 أخرى", text)
        self.assertEqual(text.count("X00"), 5)

    def test_no_limit_shows_everything(self):
        text = format_report(self.make(), limit=None)
        self.assertNotIn("أخرى", text)
        self.assertIn("X039", text)

    def test_whatsapp_sized_report_fits_message_limit(self):
        self.assertLess(len(format_report(self.make(n=400, applied=True), limit=5)), 3800)

    def test_applied_mentions_backup(self):
        self.assertIn("نسخة احتياطية", format_report(self.make(applied=True)))
        self.assertNotIn("نسخة احتياطية", format_report(self.make(applied=False)))


if __name__ == "__main__":
    unittest.main()
