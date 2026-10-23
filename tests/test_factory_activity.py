import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import require_admin
from app.routers import factories as factories_router


class FactoryActivityTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        app = FastAPI()
        app.include_router(factories_router.router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[require_admin] = lambda: None
        self.client = TestClient(app)
        self.addCleanup(self.engine.dispose)

    def test_create_and_update_factory_activity(self):
        created = self.client.post("/api/factories/", json={
            "name": "مصنع الاختبار",
            "phone_number": "+213550000000",
            "activity": "  صناعة الحجر الطبيعي  ",
        })
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["activity"], "صناعة الحجر الطبيعي")

        factory_id = created.json()["id"]
        updated = self.client.patch(f"/api/factories/{factory_id}", json={"activity": "رخام وجرانيت"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["activity"], "رخام وجرانيت")

    def test_activity_is_optional_when_creating_and_rejects_null_on_update(self):
        created = self.client.post("/api/factories/", json={
            "name": "مصنع آخر",
            "phone_number": "+213550000001",
        })
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["activity"], "")
        self.assertEqual(
            self.client.patch(f"/api/factories/{created.json()['id']}", json={"activity": None}).status_code,
            422,
        )


if __name__ == "__main__":
    unittest.main()
