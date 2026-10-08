import unittest
from unittest import mock

from app.core.config import settings
from app.services.admin_command import is_import_command

PASSWORD = "k7mq-x2ta-9rwe-p4zn"


def text_msg(body):
    return {"type": "text", "text": {"body": body}}


class ImportCommandTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("IMPORT_PASSWORD", PASSWORD), ("ADMIN_PHONE_NUMBERS", [])):
            p = mock.patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_exact_password_triggers(self):
        self.assertTrue(is_import_command("213550000000", text_msg(PASSWORD)))

    def test_case_and_surrounding_whitespace_ignored(self):
        self.assertTrue(is_import_command("213550000000", text_msg("  K7MQ-X2TA-9RWE-P4ZN \n")))

    def test_not_exact_does_not_trigger(self):
        for body in ["", "استيراد", PASSWORD + "x", "x" + PASSWORD, f"{PASSWORD} من فضلك",
                     PASSWORD[:-1], PASSWORD.replace("-", "")]:
            with self.subTest(body=body):
                self.assertFalse(is_import_command("213550000000", text_msg(body)))

    def test_non_text_messages_never_trigger(self):
        self.assertFalse(is_import_command("1", {"type": "image", "image": {"caption": PASSWORD}}))
        self.assertFalse(is_import_command("1", {"type": "text"}))
        self.assertFalse(is_import_command("1", {"type": "text", "text": None}))

    def test_disabled_when_password_empty(self):
        with mock.patch.object(settings, "IMPORT_PASSWORD", ""):
            self.assertFalse(is_import_command("1", text_msg("")))
            self.assertFalse(is_import_command("1", text_msg(PASSWORD)))

    def test_phone_restriction(self):
        with mock.patch.object(settings, "ADMIN_PHONE_NUMBERS", ["213550000000"]):
            self.assertTrue(is_import_command("213550000000", text_msg(PASSWORD)))
            self.assertTrue(is_import_command("+213 550 000 000", text_msg(PASSWORD)))
            self.assertFalse(is_import_command("213660000000", text_msg(PASSWORD)))
            self.assertFalse(is_import_command("", text_msg(PASSWORD)))


if __name__ == "__main__":
    unittest.main()
