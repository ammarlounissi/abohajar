import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.core.database import Base
from scripts.update_category_codes import update_category_codes


class UpdateCategoryCodesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        factory = models.Factory(name="Test", phone_number="123456789")
        self.db.add(factory)
        self.db.flush()
        self.db.add_all([
            models.Product(
                sku="B001", category_code="UNCATEGORIZED", title="Product B",
                price=1, primary_media_url="https://example.com/B001.jpg",
                factory_id=factory.id,
            ),
            models.Product(
                sku="PTK012", category_code="OLD", title="Product PTK",
                price=1, primary_media_url="https://example.com/PTK012.jpg",
                factory_id=factory.id,
            ),
            models.Product(
                sku="C003", category_code="C", title="Product C",
                price=1, primary_media_url="https://example.com/C003.jpg",
                factory_id=factory.id,
            ),
            models.Product(
                sku="BAD-SKU", category_code="OLD", title="Invalid SKU",
                price=1, primary_media_url="https://example.com/invalid.jpg",
                factory_id=factory.id,
            ),
        ])
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_preview_does_not_persist_and_reports_invalid_skus(self):
        summary = update_category_codes(self.db)

        self.assertEqual(summary["updated"], [
            ("B001", "UNCATEGORIZED", "B"),
            ("PTK012", "OLD", "PTK"),
        ])
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["invalid"], ["BAD-SKU"])
        self.assertEqual(self.db.get(models.Product, 1).category_code, "UNCATEGORIZED")

    def test_apply_updates_codes_and_is_idempotent(self):
        summary = update_category_codes(self.db, apply=True)

        self.assertEqual(len(summary["updated"]), 2)
        self.assertEqual(self.db.get(models.Product, 1).category_code, "B")
        self.assertEqual(self.db.get(models.Product, 2).category_code, "PTK")
        second_run = update_category_codes(self.db, apply=True)
        self.assertEqual(second_run["updated"], [])
        self.assertEqual(second_run["unchanged"], 3)
        self.assertEqual(second_run["invalid"], ["BAD-SKU"])


if __name__ == "__main__":
    unittest.main()
