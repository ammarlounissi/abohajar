import importlib.util
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "9f3c2a7b1d60_swap_product_title_category_column_names.py"
)
SPEC = importlib.util.spec_from_file_location("product_column_name_swap", MIGRATION_PATH)
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


class ProductColumnNameSwapMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        with self.engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE products ("
                "id INTEGER PRIMARY KEY, "
                "title VARCHAR(255) NOT NULL, "
                "category_code VARCHAR(20) NOT NULL)"
            ))
            connection.execute(text(
                "CREATE INDEX ix_products_category_code ON products (category_code)"
            ))
            connection.execute(text(
                "INSERT INTO products (id, title, category_code) VALUES "
                "(1, 'Product B', 'B'), (2, 'Product PTK', 'PTK')"
            ))

    def tearDown(self):
        self.engine.dispose()

    def run_migration(self, operation):
        with self.engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                operation()

    def test_upgrade_renames_columns_without_moving_values(self):
        self.run_migration(MIGRATION.upgrade)

        inspector = inspect(self.engine)
        self.assertEqual(
            {column["name"] for column in inspector.get_columns("products")},
            {"id", "title", "category_code"},
        )
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT title, category_code FROM products ORDER BY id")
            ).all()
        self.assertEqual(rows, [("B", "Product B"), ("PTK", "Product PTK")])
        indexes = inspector.get_indexes("products")
        category_index = next(index for index in indexes if index["name"] == "ix_products_category_code")
        self.assertEqual(category_index["column_names"], ["title"])

    def test_downgrade_restores_original_column_names_and_values(self):
        self.run_migration(MIGRATION.upgrade)
        self.run_migration(MIGRATION.downgrade)

        with self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT title, category_code FROM products ORDER BY id")
            ).all()
        self.assertEqual(rows, [("Product B", "B"), ("Product PTK", "PTK")])
        indexes = inspect(self.engine).get_indexes("products")
        category_index = next(index for index in indexes if index["name"] == "ix_products_category_code")
        self.assertEqual(category_index["column_names"], ["category_code"])


if __name__ == "__main__":
    unittest.main()
