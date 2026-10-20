import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import models
from app.core.database import Base
from scripts.seed_product_catalog import build_tags, read_catalog, seed_catalog


class ProductCatalogSeedTests(unittest.TestCase):
    def test_build_tags_from_structured_fields(self):
        row = {
            "الصنف": "طبيعي",
            "نوع الحجر الطبيعي": "أردواز",
            "اللون الأساسي": "رمادي",
            "الملمس": "خشن",
            "المقاس": "18×56",
            "الاستخدام": "داخلي ,خارجي",
            "السطح": "جدران,ارضيات",
            "نمط اللون": "مختلط",
        }
        self.assertEqual(
            build_tags(row),
            ["حجر طبيعي", "أردواز", "ألوان مختلطة", "درجات رمادي", "ملمس خشن", "مقاس 18×56", "داخلي", "خارجي", "جدران", "أرضيات"],
        )

    def test_read_catalog_uses_tags_from_workbook(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "المنتجات"
        sheet.append(["SKU", "الاسم التجاري", "المقاس", "الوصف", "الوسوم", "الصنف"])
        sheet.append(["B001", "الريف", "18×56", "وصف تجريبي", "حجر طبيعي | بيج | خارجي", "طبيعي"])
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.xlsx"
            workbook.save(path)
            rows = read_catalog(path)
        self.assertEqual(rows, [{
            "sku": "B001",
            "commercial_name": "الريف",
            "sizes": "18×56",
            "description": "وصف تجريبي",
            "tags": ["حجر طبيعي", "بيج", "خارجي"],
        }])

    def test_seed_updates_only_catalog_fields(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False)
        with Session() as db:
            factory = models.Factory(name="Factory", phone_number="+10000000000")
            db.add(factory)
            db.flush()
            product = models.Product(
                sku="B001", category_code="UNCATEGORIZED", tags=["قديم"], title="Original title",
                description="Keep this description", price=100.0, primary_media_url="https://example.test/1.jpg",
                factory_id=factory.id,
            )
            db.add(product)
            db.commit()

            rows = [{"sku": "B001", "commercial_name": "الريف", "sizes": "18×56", "description": "الوصف الجديد", "tags": ["حجر طبيعي", "بيج"]}]
            result = seed_catalog(db, rows)
            self.assertEqual(result["updated"], 1)
            refreshed = db.query(models.Product).filter_by(sku="B001").one()
            self.assertEqual(refreshed.commercial_name, "الريف")
            self.assertEqual(refreshed.sizes, "18×56")
            self.assertEqual(refreshed.tags, ["حجر طبيعي", "بيج"])
            self.assertEqual(refreshed.category_code, "UNCATEGORIZED")
            self.assertEqual(refreshed.description, "الوصف الجديد")

            result = seed_catalog(db, rows)
            self.assertEqual(result["unchanged"], 1)
        Base.metadata.drop_all(engine)
        engine.dispose()

    def test_json_serializer_keeps_arabic_characters_readable(self):
        engine = create_engine(
            "sqlite:///:memory:",
            json_serializer=lambda value: json.dumps(value, ensure_ascii=False),
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False)
        with Session() as db:
            factory = models.Factory(name="Factory", phone_number="+10000000001")
            db.add(factory)
            db.flush()
            db.add(models.Product(
                sku="B002", category_code="UNCATEGORIZED", tags=["حجر طبيعي", "حجر جبلي"],
                title="Test product", price=100.0, primary_media_url="https://example.test/2.jpg",
                factory_id=factory.id,
            ))
            db.commit()
            raw = db.execute(text("SELECT tags FROM products WHERE sku = 'B002'")).scalar_one()
            self.assertIn("حجر طبيعي", raw)
            self.assertNotIn("\\u062", raw)
            loaded = db.query(models.Product).filter_by(sku="B002").one()
            self.assertEqual(loaded.tags, ["حجر طبيعي", "حجر جبلي"])
        Base.metadata.drop_all(engine)
        engine.dispose()

    def test_rewrite_tag_encoding_migrates_legacy_escaped_json(self):
        engine = create_engine(
            "sqlite:///:memory:",
            json_serializer=lambda value: json.dumps(value, ensure_ascii=False),
        )
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False)
        with Session() as db:
            factory = models.Factory(name="Factory", phone_number="+10000000002")
            db.add(factory)
            db.flush()
            product = models.Product(
                sku="B003", category_code="UNCATEGORIZED", tags=["حجر طبيعي"], title="Test product",
                price=100.0, primary_media_url="https://example.test/3.jpg", factory_id=factory.id,
                sync_status=models.SyncStatus.SYNCED,
            )
            db.add(product)
            db.commit()
            # Simulate the previous SQLAlchemy JSON serializer's escaped text in SQLite.
            escaped = json.dumps(["حجر طبيعي"], ensure_ascii=True)
            db.execute(text("UPDATE products SET tags = :tags WHERE sku = 'B003'"), {"tags": escaped})
            db.commit()
            db.expire_all()
            rows = [{"sku": "B003", "commercial_name": "", "sizes": "", "description": "", "tags": ["حجر طبيعي"]}]
            summary = seed_catalog(db, rows, rewrite_tag_encoding=True)
            self.assertEqual(summary["tags_reencoded"], 1)
            raw = db.execute(text("SELECT tags FROM products WHERE sku = 'B003'")).scalar_one()
            self.assertIn("حجر طبيعي", raw)
            self.assertNotIn("\\u062", raw)
            loaded = db.query(models.Product).filter_by(sku="B003").one()
            self.assertEqual(loaded.tags, ["حجر طبيعي"])
            self.assertEqual(loaded.sync_status, models.SyncStatus.SYNCED)
        Base.metadata.drop_all(engine)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
