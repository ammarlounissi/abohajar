"""CRUD and daily dispatch for automated factory reel schedules."""
from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from app import models
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.services import factory_reel_requests
from app.services.errors import InvalidInputError, NotFoundError


def _get_schedule(db: Session, factory_id: int, schedule_id: int) -> models.FactoryReelSchedule:
    schedule = db.query(models.FactoryReelSchedule).filter_by(
        id=schedule_id, factory_id=factory_id,
    ).first()
    if schedule is None:
        raise NotFoundError("مهمة الجدولة غير موجودة لهذا المصنع")
    return schedule


def create_schedule(db: Session, factory_id: int, data: dict) -> models.FactoryReelSchedule:
    factory = db.get(models.Factory, factory_id)
    if factory is None:
        raise NotFoundError("المصنع غير موجود")
    start_date = data.pop("start_date", None) or datetime.now(ZoneInfo(data["timezone"])).date()
    stop_date = data.get("stop_date")
    if stop_date and stop_date < start_date:
        raise InvalidInputError("تاريخ التوقف يجب أن يكون في أو بعد تاريخ الانطلاق")
    value = data.pop("daily_time", time(1, 0))
    daily_time = value.strftime("%H:%M") if isinstance(value, time) else str(value)
    schedule = models.FactoryReelSchedule(
        factory_id=factory_id, start_date=start_date, daily_time=daily_time, **data,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


def list_schedules(db: Session, factory_id: int) -> list[models.FactoryReelSchedule]:
    if db.get(models.Factory, factory_id) is None:
        raise NotFoundError("المصنع غير موجود")
    return db.query(models.FactoryReelSchedule).filter_by(factory_id=factory_id).order_by(
        models.FactoryReelSchedule.id.desc(),
    ).all()


def update_schedule(
    db: Session, factory_id: int, schedule_id: int, updates: dict,
) -> models.FactoryReelSchedule:
    schedule = _get_schedule(db, factory_id, schedule_id)
    for field in ("start_date", "stop_date"):
        if field in updates and updates[field] is None and field == "start_date":
            updates.pop(field)
    start_date = updates.get("start_date", schedule.start_date)
    stop_date = updates.get("stop_date", schedule.stop_date)
    if stop_date is not None and stop_date < start_date:
        raise InvalidInputError("تاريخ التوقف يجب أن يكون في أو بعد تاريخ الانطلاق")
    if "daily_time" in updates and updates["daily_time"] is not None:
        value = updates["daily_time"]
        updates["daily_time"] = value.strftime("%H:%M") if isinstance(value, time) else str(value)
    for key, value in updates.items():
        setattr(schedule, key, value)
    db.commit()
    db.refresh(schedule)
    return schedule


def delete_schedule(db: Session, factory_id: int, schedule_id: int) -> None:
    schedule = _get_schedule(db, factory_id, schedule_id)
    db.delete(schedule)
    db.commit()


def dispatch_due_schedules(
    session_factory=SessionLocal, *, now: Optional[datetime] = None,
) -> int:
    """Queue each enabled schedule at most once per local date; safe across concurrent scheduler replicas."""
    now = now or utc_now()
    # Candidate retrieval uses each record's own time zone; a claim UPDATE wins in only one process.
    with session_factory() as db:
        candidates = db.query(models.FactoryReelSchedule).filter(
            models.FactoryReelSchedule.is_active.is_(True),
        ).order_by(models.FactoryReelSchedule.id).all()
        schedule_ids = []
        for schedule in candidates:
            local_now = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(schedule.timezone))
            local_day = local_now.date()
            if local_day < schedule.start_date:
                continue
            if schedule.stop_date is not None and local_day > schedule.stop_date:
                continue
            hour, minute = map(int, schedule.daily_time.split(":"))
            if local_now.time().replace(tzinfo=None) < time(hour, minute):
                continue
            if schedule.last_run_on == local_day:
                continue
            schedule_ids.append((schedule.id, schedule.factory_id, schedule.model, local_day))

    dispatched = 0
    for schedule_id, factory_id, model, local_day in schedule_ids:
        with session_factory() as db:
            claim = db.execute(
                update(models.FactoryReelSchedule)
                .where(
                    models.FactoryReelSchedule.id == schedule_id,
                    models.FactoryReelSchedule.is_active.is_(True),
                    or_(models.FactoryReelSchedule.last_run_on.is_(None),
                        models.FactoryReelSchedule.last_run_on < local_day),
                )
                .values(last_run_on=local_day, last_run_at=now, last_status="processing", last_error=None)
            )
            db.commit()
            if claim.rowcount != 1:
                continue
            try:
                request = factory_reel_requests.enqueue_reel_request(db, factory_id, model)
                schedule = db.get(models.FactoryReelSchedule, schedule_id)
                schedule.last_status = "queued"
                schedule.last_reel_request_id = request.id
                schedule.last_error = None
                db.commit()
                dispatched += 1
            except Exception as exc:
                db.rollback()
                schedule = db.get(models.FactoryReelSchedule, schedule_id)
                if schedule is not None:
                    schedule.last_status = "failed"
                    schedule.last_error = str(exc)[:2000]
                    db.commit()
    return dispatched
