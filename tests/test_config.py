import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.config import _get_contacts, settings


class ValidateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        values = {name: "x" for name in settings.REQUIRED}
        values["MEDIA_ROOT"] = Path(tmp.name)
        values["IMPORT_PASSWORD"] = ""
        for name, value in values.items():
            p = mock.patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_valid_without_password(self):
        settings.validate()

    def test_short_password_rejected(self):
        with mock.patch.object(settings, "IMPORT_PASSWORD", "short-pass"):
            with self.assertRaises(RuntimeError):
                settings.validate()

    def test_long_password_accepted(self):
        with mock.patch.object(settings, "IMPORT_PASSWORD", "k7mq-x2ta-9rwe-p4zn"):
            settings.validate()

    def test_missing_media_root_dir_rejected(self):
        with mock.patch.object(settings, "MEDIA_ROOT", Path("/nonexistent/xyz")):
            with self.assertRaises(RuntimeError):
                settings.validate()


class ProductLinkTests(unittest.TestCase):
    def link(self, template):
        with mock.patch.object(settings, "PRODUCT_LINK_TEMPLATE", template):
            return settings.product_link("B001")

    def test_uppercase_placeholder(self):
        self.assertEqual(self.link("https://hadjretbladi.com/{SKU}/"), "https://hadjretbladi.com/B001/")

    def test_lowercase_placeholder_still_works(self):
        self.assertEqual(self.link("https://hadjretbladi.com/{sku}/"), "https://hadjretbladi.com/B001/")


class AdminContactsParsingTests(unittest.TestCase):
    def parse(self, value):
        with mock.patch.dict("os.environ", {"X_CONTACTS": value}):
            return _get_contacts("X_CONTACTS")

    def test_empty(self):
        self.assertEqual(self.parse(""), {})

    def test_multiple_channels_and_normalization(self):
        self.assertEqual(
            self.parse("telegram:123, WhatsApp:+213 55-000 0000 ,telegram:456"),
            {"telegram": ["123", "456"], "whatsapp": ["213550000000"]},
        )

    def test_malformed_items_ignored(self):
        self.assertEqual(self.parse("telegram,:5,whatsapp:"), {})


if __name__ == "__main__":
    unittest.main()
