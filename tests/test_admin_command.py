import unittest
from unittest import mock

from app.core.config import settings
from app.services.admin_command import is_import_command

PASSWORD = "k7mq-x2ta-9rwe-p4zn"
WA = "whatsapp"
TG = "telegram"


class ImportCommandTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("IMPORT_PASSWORD", PASSWORD), ("ADMIN_PHONE_NUMBERS", []), ("ADMIN_CONTACTS", {})):
            p = mock.patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_exact_password_triggers(self):
        self.assertTrue(is_import_command(WA, "213550000000", PASSWORD))

    def test_case_and_surrounding_whitespace_ignored(self):
        self.assertTrue(is_import_command(WA, "213550000000", "  K7MQ-X2TA-9RWE-P4ZN \n"))

    def test_not_exact_does_not_trigger(self):
        for body in ["", "استيراد", PASSWORD + "x", "x" + PASSWORD, f"{PASSWORD} من فضلك",
                     PASSWORD[:-1], PASSWORD.replace("-", "")]:
            with self.subTest(body=body):
                self.assertFalse(is_import_command(WA, "213550000000", body))

    def test_non_text_never_triggers(self):
        self.assertFalse(is_import_command(WA, "1", None))

    def test_disabled_when_password_empty(self):
        with mock.patch.object(settings, "IMPORT_PASSWORD", ""):
            self.assertFalse(is_import_command(WA, "1", ""))
            self.assertFalse(is_import_command(WA, "1", PASSWORD))

    def test_whatsapp_phone_restriction(self):
        with mock.patch.object(settings, "ADMIN_PHONE_NUMBERS", ["213550000000"]):
            self.assertTrue(is_import_command(WA, "213550000000", PASSWORD))
            self.assertTrue(is_import_command(WA, "+213 550 000 000", PASSWORD))
            self.assertFalse(is_import_command(WA, "213660000000", PASSWORD))
            self.assertFalse(is_import_command(WA, "", PASSWORD))

    def test_works_from_any_channel_when_unrestricted(self):
        self.assertTrue(is_import_command(TG, "123456789", PASSWORD))

    def test_telegram_restriction_via_admin_contacts(self):
        with mock.patch.object(settings, "ADMIN_CONTACTS", {TG: ["123456789"]}):
            self.assertTrue(is_import_command(TG, "123456789", PASSWORD))
            self.assertFalse(is_import_command(TG, "987654321", PASSWORD))

    def test_restriction_is_per_channel(self):
        # قيّدنا تيليجرام فقط: واتساب يبقى بلا قيد (الكلمة تكفي)، وتيليجرام لا يقبل غير المسجَّل
        with mock.patch.object(settings, "ADMIN_CONTACTS", {TG: ["123456789"]}):
            self.assertTrue(is_import_command(WA, "213550000000", PASSWORD))
            self.assertFalse(is_import_command(TG, "1", PASSWORD))


if __name__ == "__main__":
    unittest.main()
