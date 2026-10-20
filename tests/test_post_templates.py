"""قوالب نص المنتج لكل منصة: الشكل، غياب الروابط في إنستغرام، وحدّ إنستغرام."""
import unittest
from types import SimpleNamespace
from unittest import mock

from app.core.config import settings
from app.services import post_templates
from app.services.post_templates import build_caption, intl_phone

PHONE = "0549194378"


def product(**kw):
    base = dict(sku="F020", commercial_name="فاراديرو اغبر", title="x", description="كسوة حجرية",
                tags=["حجر صناعي", "ألوان مختلطة", "درجات أبيض", "رابع"])
    return SimpleNamespace(**{**base, **kw})


class TemplateTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(settings, "PRODUCT_LINK_TEMPLATE", "https://hadjretbladi.com/{SKU}/")
        p.start()
        self.addCleanup(p.stop)

    def test_facebook_has_whatsapp_and_product_links(self):
        text = build_caption(product(), "facebook", PHONE)
        lines = text.split("\n")
        self.assertEqual(lines[0], "📌 حجر فاراديرو اغبر")
        self.assertEqual(lines[1], "كسوة حجرية")
        self.assertIn(f"https://wa.me/213549194378?text=", text)
        self.assertIn("🔗 تفاصيل المنتج: https://hadjretbladi.com/F020/", text)
        self.assertTrue(lines[-1].startswith("#حجرة_بلادي #ديكور_جزائري #الجزائر #حجر_صناعي"))

    def test_instagram_has_no_links_but_keeps_number_and_code(self):
        text = build_caption(product(), "instagram", PHONE)
        self.assertNotIn("http", text)
        self.assertIn(f"📲 للطلب أو الاستفسار عبر واتساب: {PHONE}", text)
        self.assertIn("🏷️ رمز المنتج: F020", text)

    def test_unknown_platform_uses_links_template(self):
        self.assertEqual(build_caption(product(), "telegram", PHONE), build_caption(product(), "facebook", PHONE))

    def test_title_prefix_not_duplicated_and_empty_description_skipped(self):
        text = build_caption(product(commercial_name="حجر بني", description=None), "facebook", PHONE)
        self.assertEqual(text.split("\n")[0], "📌 حجر بني")
        self.assertNotIn("None", text)
        self.assertEqual(text.split("\n")[1].split(":")[0], "📲 للطلب أو الاستفسار عبر واتساب")

    def test_only_three_product_tags_and_no_duplicates(self):
        tags = post_templates.hashtags(product(tags=["#أ", "أ", "ب", "ج", "د"])).split()
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual(len(tags), 3 + 3)

    def test_default_phone_comes_from_settings(self):
        with mock.patch.object(settings, "POST_CONTACT_PHONE", "0550000000"):
            self.assertIn("0550000000", build_caption(product(), "instagram"))

    def test_intl_phone(self):
        self.assertEqual(intl_phone("0549194378"), "213549194378")
        self.assertEqual(intl_phone("+213 549 194 378"), "213549194378")

    def test_instagram_caption_within_limit(self):
        self.assertLess(len(build_caption(product(description="ا" * 500), "instagram", PHONE)), 2200)


if __name__ == "__main__":
    unittest.main()
