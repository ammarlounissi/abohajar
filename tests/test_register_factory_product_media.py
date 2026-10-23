import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import models
from app.core.database import Base
from scripts.register_factory_product_media import register_product_images


class RegisterFactoryProductMediaTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.db.add_all([
            models.Factory(id=4, name="Factory 4", phone_number="123456789"),
            models.Factory(id=5, name="Factory 5", phone_number="123456788"),
        ])
        self.db.flush()
        self.db.add_all([
            models.Product(
                sku="A001", category_code="A", title="Product A", price=1,
                primary_media_url="https://example.test/a.jpg",
                additional_media_urls=[
                    "https://example.test/a-2.jpg",
                    "https://example.test/a.jpg",
                ],
                factory_id=4,
            ),
            models.Product(
                sku="A002", category_code="A", title="Product B", price=1,
                primary_media_url="https://example.test/b.jpg",
                additional_media_urls=[],
                factory_id=4,
            ),
            models.Product(
                sku="B001", category_code="B", title="Other factory product", price=1,
                primary_media_url="https://example.test/other.jpg",
                additional_media_urls=[],
                factory_id=5,
            ),
        ])
        self.db.commit()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

    def test_preview_does_not_insert_media_records(self):
        summary = register_product_images(self.db)

        self.assertEqual(summary["products"], 2)
        self.assertEqual(summary["image_urls_found"], 4)
        self.assertEqual(len(summary["added"]), 3)
        self.assertEqual(summary["duplicate_product_urls"], 1)
        self.assertEqual(self.db.scalars(select(models.Media)).all(), [])

    def test_apply_registers_only_factory_images_and_is_idempotent(self):
        summary = register_product_images(self.db, apply=True)

        self.assertEqual(len(summary["added"]), 3)
        media = self.db.scalars(
            select(models.Media).where(models.Media.factory_id == 4)
        ).all()
        self.assertEqual(
            {item.url for item in media},
            {
                "https://example.test/a.jpg",
                "https://example.test/a-2.jpg",
                "https://example.test/b.jpg",
            },
        )
        self.assertTrue(all(item.media_type == "image" for item in media))

        second_run = register_product_images(self.db, apply=True)
        self.assertEqual(second_run["added"], [])
        self.assertEqual(second_run["already_registered"], 4)
        self.assertEqual(len(self.db.scalars(select(models.Media)).all()), 3)


if __name__ == "__main__":
    unittest.main()
