"""اختبارات app/services/bot_text.py (دوال صافية). التشغيل:  python -m unittest discover -s tests -v"""
import unittest

from app.services import bot_text as t


class PriceTests(unittest.TestCase):
    def test_valid_prices(self):
        for raw, expected in [("3200", 3200), ("3 200", 3200), ("٣٢٠٠", 3200), ("3200,50", 3200.5),
                              ("3200 دج", 3200), ("3200DA", 3200), ("12.5", 12.5)]:
            with self.subTest(raw=raw):
                self.assertEqual(t.parse_price(raw), expected)

    def test_invalid_prices(self):
        for raw in ["", "abc", "0", "-5", "12.345", "1e5", "10 20 30x", "999999999999", None]:
            with self.subTest(raw=raw):
                self.assertIsNone(t.parse_price(raw))


class IndexTests(unittest.TestCase):
    def test_index_range(self):
        self.assertEqual(t.parse_index("2", 3), 2)
        self.assertEqual(t.parse_index("٣", 3), 3)
        for raw in ["0", "4", "x", "", "1.5", "-1", "1000"]:
            with self.subTest(raw=raw):
                self.assertIsNone(t.parse_index(raw, 3))


class TextTests(unittest.TestCase):
    def test_commands_normalized(self):
        self.assertIn(t.norm_command("  إلغاء! "), t.CANCEL_WORDS)
        self.assertIn(t.norm_command("Menu"), t.MENU_WORDS)
        self.assertIn(t.norm_command("رجوع."), t.BACK_WORDS)

    def test_title_and_description_bounds(self):
        self.assertEqual(t.clean_title("  حجر   قالمة  "), "حجر قالمة")
        self.assertIsNone(t.clean_title("a"))
        self.assertIsNone(t.clean_title("x" * 256))
        self.assertIsNone(t.clean_description("   "))
        self.assertIsNone(t.clean_description("x" * 1501))

    def test_next_sku_number(self):
        self.assertEqual(t.next_number([], "B"), 1)
        self.assertEqual(t.next_number(["B001", "B013", "BK020", "F099"], "B"), 14)   # BK020 ليس من الصنف B
        self.assertEqual(t.format_sku("B", 14), "B014")
        self.assertEqual(t.format_sku("PTK", 7), "PTK007")


if __name__ == "__main__":
    unittest.main()
