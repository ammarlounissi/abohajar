import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.config import settings


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


if __name__ == "__main__":
    unittest.main()
