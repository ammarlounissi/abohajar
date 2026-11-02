import unittest
from datetime import date, datetime, time
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, schemas
from app.core.database import Base
from app.services import factory_reel_schedules as schedules


class FactoryReelScheduleTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.engine = engine
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add(models.Factory(
                id=1, name="مصنع الأحجار", phone_number="213550000000", activity="حجر طبيعي",
            ))
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def create(self, **overrides):
        data = schemas.FactoryReelScheduleCreate(**overrides).model_dump()
        data["start_date"] = data.pop("start_date") or date(2026, 11, 2)
        with self.Session() as db:
            return schedules.create_schedule(db, 1, data).id

    def test_defaults(self):
        body = schemas.FactoryReelScheduleCreate()
        self.assertEqual(body.model, "gemini-3.1-flash-lite")
        self.assertEqual(body.daily_time, time(1, 0))
        self.assertEqual(body.timezone, "Africa/Algiers")
        self.assertTrue(body.is_active)
        self.assertIsNone(body.stop_date)

    def test_invalid_end_date_is_rejected(self):
        with self.assertRaises(ValueError):
            schemas.FactoryReelScheduleCreate(
                start_date=date(2026, 11, 3), stop_date=date(2026, 11, 2),
            )

    def test_schedule_dispatches_once_when_local_daily_time_is_reached(self):
        schedule_id = self.create(daily_time="01:00")
        with mock.patch(
            "app.services.factory_reel_schedules.factory_reel_requests.enqueue_reel_request",
            return_value=type("Request", (), {"id": 44})(),
        ) as enqueue:
            # 00:01 UTC = 01:01 in Algeria in November.
            now = datetime(2026, 11, 2, 0, 1)
            self.assertEqual(schedules.dispatch_due_schedules(self.Session, now=now), 1)
            self.assertEqual(schedules.dispatch_due_schedules(self.Session, now=now), 0)
        enqueue.assert_called_once_with(mock.ANY, 1, "gemini-3.1-flash-lite")
        with self.Session() as db:
            stored = db.get(models.FactoryReelSchedule, schedule_id)
            self.assertEqual(stored.last_run_on, date(2026, 11, 2))
            self.assertEqual(stored.last_status, "queued")
            self.assertEqual(stored.last_reel_request_id, 44)

    def test_not_due_before_time_and_inactive_or_outside_date_range(self):
        future = self.create(daily_time="02:00")
        inactive = self.create(is_active=False)
        expired = self.create(start_date=date(2026, 11, 1), stop_date=date(2026, 11, 1))
        with mock.patch(
            "app.services.factory_reel_schedules.factory_reel_requests.enqueue_reel_request",
        ) as enqueue:
            # 00:01 UTC = 01:01 Algeria; the active schedule remains 59 minutes early.
            self.assertEqual(schedules.dispatch_due_schedules(
                self.Session, now=datetime(2026, 11, 2, 0, 1),
            ), 0)
        enqueue.assert_not_called()
        with self.Session() as db:
            self.assertIsNone(db.get(models.FactoryReelSchedule, future).last_run_on)
            self.assertIsNone(db.get(models.FactoryReelSchedule, inactive).last_run_on)
            self.assertIsNone(db.get(models.FactoryReelSchedule, expired).last_run_on)

    def test_dispatch_failure_is_recorded_and_not_retried_until_next_day(self):
        schedule_id = self.create()
        with mock.patch(
            "app.services.factory_reel_schedules.factory_reel_requests.enqueue_reel_request",
            side_effect=RuntimeError("missing images"),
        ) as enqueue:
            now = datetime(2026, 11, 2, 0, 1)
            self.assertEqual(schedules.dispatch_due_schedules(self.Session, now=now), 0)
            self.assertEqual(schedules.dispatch_due_schedules(self.Session, now=now), 0)
        enqueue.assert_called_once()
        with self.Session() as db:
            stored = db.get(models.FactoryReelSchedule, schedule_id)
            self.assertEqual(stored.last_status, "failed")
            self.assertIn("missing images", stored.last_error)


if __name__ == "__main__":
    unittest.main()
