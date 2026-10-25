"""Queue and lifecycle management for durable factory video generation jobs."""
import logging
import random
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy.orm import Session

from app import models
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.services import factory_videos
from app.services import video_maker
from app.services.errors import InvalidInputError, NotFoundError, ServiceError

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]
INTERRUPTED_MESSAGE = "توقف عامل التوليد أثناء تنفيذ المهمة؛ تحقق من المكتبة قبل إعادة الطلب"


def enqueue_video_job(
    db: Session,
    factory_id: int,
    *,
    source: str,
    sku: Optional[str],
    template: str,
    duration: Optional[float],
    audio: Optional[str],
) -> models.FactoryVideoJob:
    selected_template, selected_duration, selected_audio = (
        factory_videos.validate_factory_video_request(
            db, factory_id, source=source, sku=sku, template_id=template,
            duration=duration, audio=audio,
        )
    )
    job = models.FactoryVideoJob(
        factory_id=factory_id,
        source=source,
        sku=sku,
        template=selected_template.id,
        duration=selected_duration,
        audio=selected_audio,
        status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def enqueue_automated_library_video(
    db: Session, factory_id: int,
) -> models.FactoryVideoJob:
    """Queue a 30-second library video with a randomly selected template and soundtrack."""
    audio_files = video_maker.list_audio_files()
    if not audio_files:
        raise InvalidInputError("لا توجد ملفات صوتية متاحة للاختيار العشوائي")
    template = random.choice(video_maker.VIDEO_TEMPLATES)
    audio = random.choice(audio_files)
    return enqueue_video_job(
        db,
        factory_id,
        source="library",
        sku=None,
        template=template.id,
        duration=30,
        audio=audio,
    )


def get_video_job(db: Session, factory_id: int, job_id: int) -> models.FactoryVideoJob:
    job = db.query(models.FactoryVideoJob).filter(
        models.FactoryVideoJob.id == job_id,
        models.FactoryVideoJob.factory_id == factory_id,
    ).first()
    if job is None:
        raise NotFoundError("مهمة توليد الفيديو غير موجودة لهذا المصنع")
    return job


def claim_next_video_job(session_factory: SessionFactory = SessionLocal) -> Optional[int]:
    """Atomically claim one queued task so multiple workers cannot render it twice."""
    with session_factory() as db:
        job_id = db.query(models.FactoryVideoJob.id).filter(
            models.FactoryVideoJob.status == "queued",
        ).order_by(models.FactoryVideoJob.id).limit(1).scalar()
        if job_id is None:
            return None
        updated = db.query(models.FactoryVideoJob).filter(
            models.FactoryVideoJob.id == job_id,
            models.FactoryVideoJob.status == "queued",
        ).update(
            {
                models.FactoryVideoJob.status: "running",
                models.FactoryVideoJob.started_at: utc_now(),
                models.FactoryVideoJob.updated_at: utc_now(),
            },
            synchronize_session=False,
        )
        db.commit()
        return job_id if updated == 1 else None


def process_video_job(job_id: int, session_factory: SessionFactory = SessionLocal) -> bool:
    """Run an already-claimed task. Failed jobs are terminal and never auto-retried."""
    with session_factory() as db:
        job = db.get(models.FactoryVideoJob, job_id)
        if job is None or job.status != "running":
            return False
        request = {
            "factory_id": job.factory_id,
            "source": job.source,
            "sku": job.sku,
            "template_id": job.template,
            "duration": job.duration,
            "audio": job.audio,
        }

    try:
        with session_factory() as db:
            result = factory_videos.generate_factory_video(db, **request)
        with session_factory() as db:
            job = db.get(models.FactoryVideoJob, job_id)
            if job is None:
                logger.error("Video job %s disappeared after generation", job_id)
                return False
            job.status = "succeeded"
            job.sku = result.sku
            job.video_url = result.url
            job.size_bytes = result.size_bytes
            job.images_used = result.images_used
            job.updated_at = utc_now()
            job.completed_at = utc_now()
            db.commit()
        logger.info("Factory video job %s completed: %s", job_id, result.url)
        return True
    except ServiceError as exc:
        _fail_job(job_id, str(exc), session_factory)
        logger.warning("Factory video job %s failed: %s", job_id, exc)
        return False
    except Exception:
        logger.exception("Factory video job %s failed unexpectedly", job_id)
        _fail_job(
            job_id, "حدث خطأ أثناء توليد الفيديو؛ راجع سجل العامل", session_factory,
        )
        return False


def _fail_job(job_id: int, message: str, session_factory: SessionFactory) -> None:
    with session_factory() as db:
        job = db.get(models.FactoryVideoJob, job_id)
        if job is None:
            logger.error("Cannot record failure: video job %s disappeared", job_id)
            return
        job.status = "failed"
        job.error = message
        job.updated_at = utc_now()
        job.completed_at = utc_now()
        db.commit()


def recover_interrupted_video_jobs(session_factory: SessionFactory = SessionLocal) -> int:
    """Mark previously running jobs as failed on worker startup; never duplicate output."""
    now: datetime = utc_now()
    with session_factory() as db:
        count = db.query(models.FactoryVideoJob).filter(
            models.FactoryVideoJob.status == "running",
        ).update(
            {
                models.FactoryVideoJob.status: "failed",
                models.FactoryVideoJob.error: INTERRUPTED_MESSAGE,
                models.FactoryVideoJob.updated_at: now,
                models.FactoryVideoJob.completed_at: now,
            },
            synchronize_session=False,
        )
        db.commit()
        return count
