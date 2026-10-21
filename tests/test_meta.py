import unittest
from types import SimpleNamespace
from unittest import mock

from app.models import Availability
from app.core.config import settings
from app.services.meta import _build_payload


def product(**overrides):
    values = {
        "sku": "B001",
        "title": "Product title",
        "description": "Description",
        "availability": Availability.IN_STOCK,
        "condition": "new",
        "price": 125.5,
        "currency": "DZD",
        "primary_media_url": "https://example.com/B001.jpg",
        "brand": "Brand",
        "sizes": None,
        "tags": [],
        "category_code": "B",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class MetaPayloadTests(unittest.TestCase):
    def test_maps_sizes_and_first_five_tags_without_changing_name(self):
        item = product(
            sizes="18×56",
            tags=[" حجر طبيعي ", "بيج", "", "خارجي", "ملمس خشن", "تجاهل 6"],
        )
        with mock.patch.object(settings, "product_link", return_value="https://example.com/B001"):
            payload = _build_payload(item)

        self.assertEqual(payload["name"], "Product title")
        self.assertEqual(payload["size"], "18×56")
        self.assertEqual(
            [payload[f"custom_label_{index}"] for index in range(5)],
            ["حجر طبيعي", "بيج", "خارجي", "ملمس خشن", "تجاهل 6"],
        )
        self.assertNotIn("category_code", payload)
        self.assertNotIn("product_type", payload)

    def test_omits_empty_size_and_tags(self):
        with mock.patch.object(settings, "product_link", return_value="https://example.com/B001"):
            payload = _build_payload(product(sizes=" ", tags=["", "  "]))

        self.assertNotIn("size", payload)
        self.assertFalse(any(key.startswith("custom_label_") for key in payload))


if __name__ == "__main__":
    unittest.main()
