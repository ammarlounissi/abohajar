"""نسخ صور وفيديوهات مجلدات Google Drive التي يشاركها المصنع مع حساب المشروع إلى مكتبة وسائطه (عبر rclone).

التعرف على مجلد المصنع: المجلد المشارَك الذي مالكه (owner) هو بريد المصنع المسجَّل كجهة اتصال
channel=email. المالك لا يمكن انتحاله، فلا يستطيع غريب شارك مجلداً أن يضيف صوراً لمصنع.
لكل ملف جديد: تنزيل مؤقت -> تحقق (حجم/صورة سليمة بـ PIL، فيديو سليم بـ ffprobe) -> media_library.upload_factory_media (يحفظ الملف
ويكتب رابطه في جدول media) -> تسجيلها في drive_synced_files كي لا تتكرر. الشبكة وrclone معزولان في
دوال صغيرة (list_shared_folders / list_folder_media / download_media) ليسهل استبدالها في الاختبار.
اتفاقية الحفظ: sync_* تنفّذ commit بنفسها."""
import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PIL import Image, UnidentifiedImageError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.models import Channel
from app.services import media_library
from app.services.ffmpeg_tools import find_tool
from app.services.errors import InvalidInputError, NotFoundError, ServiceError
from app.services.factories import get_factory

logger = logging.getLogger(__name__)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = media_library.MAX_UPLOAD_BYTES      # 100 ميغابايت، سقف مكتبة المصنع نفسها
PROBE_TIMEOUT = 60
VIDEO_DOWNLOAD_TIMEOUT = 900
MAX_IMAGE_PIXELS = 50_000_000
STALE_COPYING_MINUTES = 30
LIST_TIMEOUT = 120
DOWNLOAD_TIMEOUT = 300
SessionFactory = Callable[[], Session]


class DriveSyncError(ServiceError):
    """تعذّر تشغيل rclone أو قراءة Drive (rclone غير مثبت، remote غير مُعدّ، انتهت المهلة...)."""


class SkipFile(Exception):
    """ملف لن يُنسخ أبداً (ليس صورة/فيديو سليماً، أو كبير جداً). يُسجَّل skipped فلا يُعاد تنزيله."""


@dataclass
class SyncReport:
    factory_id: int
    folders: int = 0
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    remaining: int = 0
    errors: List[str] = field(default_factory=list)


# ---------------------------------------------------------------- rclone (الحدّ مع العالم الخارجي)

def _rclone(*args: str, timeout: int) -> str:
    command = [settings.RCLONE_BIN, *args]
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise DriveSyncError("rclone غير مثبت أو مساره خاطئ (RCLONE_BIN)") from exc
    except subprocess.TimeoutExpired as exc:
        raise DriveSyncError("انتهت مهلة rclone") from exc
    if done.returncode != 0:
        detail = (done.stderr or "").strip().splitlines()[-1:] or ["بلا تفاصيل"]
        raise DriveSyncError(f"فشل rclone (الرمز {done.returncode}): {detail[0][:300]}")
    return done.stdout


def list_shared_folders() -> List[dict]:
    """المجلدات المشارَكة مع الحساب: [{"id", "name", "owner"}]. owner بريد المالك بأحرف صغيرة (أو "")."""
    out = _rclone(
        "lsjson", f"{settings.RCLONE_REMOTE}:", "--drive-shared-with-me", "--dirs-only", "--metadata",
        timeout=LIST_TIMEOUT,
    )
    try:
        items = json.loads(out or "[]")
    except ValueError as exc:
        raise DriveSyncError("ردّ rclone ليس JSON صالحاً") from exc
    return [
        {"id": item.get("ID", ""), "name": item.get("Name", ""),
         "owner": str((item.get("Metadata") or {}).get("owner", "")).strip().lower()}
        for item in items if item.get("ID")
    ]


def media_kind(name: str, mime_type: str = "") -> Optional[str]:
    """"image" أو "video" إن كان الملف من الأنواع المدعومة (بالامتداد ونوع MIME معاً)، وإلا None."""
    extension = Path(name).suffix.lower()
    if extension in IMAGE_EXTENSIONS and (not mime_type or mime_type.startswith("image/")):
        return "image"
    if extension in VIDEO_EXTENSIONS and (not mime_type or mime_type.startswith("video/")):
        return "video"
    return None


def list_folder_media(folder_id: str) -> List[dict]:
    """صور وفيديوهات المجلد (مع الفرعية): [{"id","path","name","size","modtime","kind"}] بالأقدم أولاً."""
    out = _rclone(
        "lsjson", f"{settings.RCLONE_REMOTE}:", f"--drive-root-folder-id={folder_id}", "-R", "--files-only",
        timeout=LIST_TIMEOUT,
    )
    try:
        items = json.loads(out or "[]")
    except ValueError as exc:
        raise DriveSyncError("ردّ rclone ليس JSON صالحاً") from exc
    found = [
        {"id": item["ID"], "path": item.get("Path") or item["Name"], "name": item["Name"],
         "size": item.get("Size"), "modtime": item.get("ModTime", ""),
         "kind": media_kind(item["Name"], str(item.get("MimeType", "")))}
        for item in items if item.get("ID") and item.get("Name")
    ]
    return sorted((m for m in found if m["kind"]), key=lambda media: media["modtime"])


def download_media(folder_id: str, path: str, destination: Path, kind: str = "image") -> None:
    _rclone(
        "copyto", f"{settings.RCLONE_REMOTE}:{path}", str(destination),
        f"--drive-root-folder-id={folder_id}",
        timeout=VIDEO_DOWNLOAD_TIMEOUT if kind == "video" else DOWNLOAD_TIMEOUT,
    )


# ---------------------------------------------------------------- المنطق

def factory_email(db: Session, factory_id: int) -> Optional[str]:
    contact = db.query(models.FactoryContact).filter(
        models.FactoryContact.factory_id == factory_id, models.FactoryContact.channel == Channel.EMAIL.value,
    ).order_by(models.FactoryContact.id).first()
    return contact.identifier if contact else None


def _verify_video(path: Path, size: Optional[int]) -> None:
    if (size or path.stat().st_size) > MAX_VIDEO_BYTES:
        raise SkipFile("حجم الفيديو يتجاوز 100 ميغابايت")
    ffprobe = find_tool("ffprobe")
    if not ffprobe:
        logger.warning("ffprobe غير متوفر: نُسخ الفيديو دون فحص سلامته")
        return
    try:
        done = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT, check=False,
        )
        duration = float(done.stdout.strip().splitlines()[0]) if done.returncode == 0 and done.stdout.strip() else 0
    except (subprocess.TimeoutExpired, ValueError, OSError):
        duration = 0
    if duration <= 0:
        raise SkipFile("الملف ليس فيديو سليماً")


def _verify_image(path: Path, size: Optional[int]) -> None:
    if (size or path.stat().st_size) > MAX_IMAGE_BYTES:
        raise SkipFile("حجم الصورة يتجاوز 20 ميغابايت")
    try:
        with Image.open(path) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise SkipFile("أبعاد الصورة أكبر من الحد المسموح")
            image.verify()
    except SkipFile:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise SkipFile("الملف ليس صورة سليمة") from exc


def _recover_stale(db: Session) -> None:
    """صفوف copying العالقة (توقفت العملية أثناء النسخ) تُحذف ليُعاد نسخها."""
    cutoff = utc_now() - timedelta(minutes=STALE_COPYING_MINUTES)
    db.query(models.DriveSyncedFile).filter(
        models.DriveSyncedFile.status == "copying", models.DriveSyncedFile.updated_at < cutoff,
    ).delete(synchronize_session=False)
    db.commit()


def _copy_one(db: Session, factory_id: int, folder: dict, image: dict, report: SyncReport) -> None:
    kind = image.get("kind", "image")
    row = models.DriveSyncedFile(
        factory_id=factory_id, drive_file_id=image["id"], name=image["name"][:500],
        size_bytes=image["size"] if isinstance(image["size"], int) else None, status="copying",
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:               # نُسخ سابقاً أو تنسخه عملية أخرى الآن
        db.rollback()
        return
    row_id = row.id

    def close(status: str, error: Optional[str] = None, media_id: Optional[int] = None) -> None:
        record = db.get(models.DriveSyncedFile, row_id)
        record.status, record.error, record.media_id = status, error, media_id
        db.commit()

    try:
        with tempfile.TemporaryDirectory(prefix="drive-sync-") as tmp:
            local = Path(tmp) / f"file{Path(image['name']).suffix.lower()}"
            download_media(folder["id"], image["path"], local, kind)
            size = image["size"] if isinstance(image["size"], int) else None
            (_verify_video if kind == "video" else _verify_image)(local, size)
            with local.open("rb") as handle:
                item = media_library.upload_factory_media(
                    db, factory_id, file=handle, filename=local.name, content_type=None,
                    description=f"من Google Drive: {folder['name']}/{image['path']}"[:2000],
                )
        close("copied", media_id=item.id)
        report.copied += 1
    except SkipFile as exc:
        close("skipped", str(exc))
        report.skipped += 1
        logger.info("Drive file skipped (factory=%s, %s): %s", factory_id, image["path"], exc)
    except Exception as exc:               # فشل مؤقت: احذف الحجز ليُعاد في التشغيل التالي
        db.rollback()
        db.query(models.DriveSyncedFile).filter(models.DriveSyncedFile.id == row_id).delete()
        db.commit()
        report.failed += 1
        report.errors.append(f"{image['path']}: {exc}"[:300])
        logger.warning("Drive file failed (factory=%s, %s): %s", factory_id, image["path"], exc)


def sync_factory(
    db: Session, factory_id: int, *, folders: Optional[List[dict]] = None, limit: Optional[int] = None,
) -> SyncReport:
    """ينسخ صور مجلدات المصنع الجديدة. limit: أقصى عدد صور يُنسخ في هذا التشغيل (الباقي في report.remaining).
    folders: قائمة المجلدات المشارَكة إن كانت مُجلبة مسبقاً (وإلا تُجلب هنا)."""
    get_factory(db, factory_id)
    email = factory_email(db, factory_id)
    if not email:
        raise InvalidInputError("أضف بريد المصنع أولاً: POST /api/factories/{id}/contacts بـ channel=email")
    _recover_stale(db)

    shared = list_shared_folders() if folders is None else folders
    mine = [folder for folder in shared if folder["owner"] == email]
    report = SyncReport(factory_id=factory_id, folders=len(mine))
    known = {row[0] for row in db.query(models.DriveSyncedFile.drive_file_id).filter(
        models.DriveSyncedFile.factory_id == factory_id)}
    budget = limit
    for folder in mine:
        try:
            images = [image for image in list_folder_media(folder["id"]) if image["id"] not in known]
        except DriveSyncError as exc:
            report.errors.append(f"{folder['name']}: {exc}")
            continue
        for image in images:
            if budget is not None and budget <= 0:
                report.remaining += 1
                continue
            _copy_one(db, factory_id, folder, image, report)
            known.add(image["id"])
            if budget is not None:
                budget -= 1
    return report


def sync_all(session_factory: SessionFactory = SessionLocal) -> Dict[int, SyncReport]:
    """لكل المصانع الفعّالة التي لها بريد: يجلب المجلدات المشارَكة مرة واحدة ويُزامن. تستدعيها الخدمة الدورية."""
    with session_factory() as db:
        emails = {
            factory_id: identifier for factory_id, identifier in db.query(
                models.FactoryContact.factory_id, models.FactoryContact.identifier,
            ).join(models.Factory, models.Factory.id == models.FactoryContact.factory_id).filter(
                models.FactoryContact.channel == Channel.EMAIL.value, models.Factory.is_active.is_(True),
            )
        }
    if not emails:
        return {}
    shared = list_shared_folders()
    reports: Dict[int, SyncReport] = {}
    for factory_id in emails:
        try:
            with session_factory() as db:
                reports[factory_id] = sync_factory(db, factory_id, folders=shared)
        except (ServiceError, NotFoundError) as exc:
            logger.warning("Drive sync skipped for factory %s: %s", factory_id, exc)
    return reports
