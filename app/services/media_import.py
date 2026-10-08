"""الاستيراد التلقائي: فحص مجلد الصور + CSV ثم إضافة/تحديث المنتجات.
يُستدعى من scripts/import_from_media.py ومن أمر واتساب الإداري.
لا يحذف أي منتج، ولا يمسّ الوصف ولا المنتجات التي لا صور لها."""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from pydantic import ValidationError

from app import models, schemas
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.import_report import ImportConfigError, ImportReport
from app.services.media_scan import build_records, read_csv_rows, scan_media_root
from app.services.meta import sync_products_in_background
from app.services.products import upsert_product

logger = logging.getLogger(__name__)

COMPARED_FIELDS = ("title", "price", "primary_media_url", "additional_media_urls")


def backup_sqlite() -> Optional[Path]:
    """نسخة احتياطية بختم زمني قبل أي كتابة. نستعمل واجهة النسخ في sqlite3 لأن القاعدة بوضع WAL."""
    url = settings.DATABASE_URL
    if not url.startswith("sqlite:///"):
        logger.warning("Database is not SQLite: no automatic backup taken")
        return None
    src_path = Path(url[len("sqlite:///"):])
    if not src_path.is_file():
        return None
    dest_path = src_path.with_name(f"{src_path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    src, dest = sqlite3.connect(src_path), sqlite3.connect(dest_path)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    return dest_path


def run_import(*, apply: bool, factory_id: Optional[int] = None,
               csv_path: Optional[Path] = None) -> ImportReport:
    """apply=False: تجربة لا تكتب شيئاً. دالة متزامنة (تقرأ ملفات كثيرة)، فتُستدعى من
    المسارات غير المتزامنة عبر asyncio.to_thread. ترفع ImportConfigError عند خلل الإعداد."""
    csv_path = csv_path or settings.PRODUCTS_CSV_PATH or settings.MEDIA_ROOT / "products.csv"
    if not csv_path.is_file():
        raise ImportConfigError(f"ملف CSV غير موجود: {csv_path}")

    scan = scan_media_root(settings.MEDIA_ROOT)
    try:
        rows, csv_warnings = read_csv_rows(csv_path)
    except ValueError as exc:
        raise ImportConfigError(str(exc)) from exc
    build = build_records(scan, rows)

    # نتحقق من كل سجل بنفس مخطط الـ API (السعر > 0، العنوان، الرمز...)
    valid: List[schemas.ProductBase] = []
    report = ImportReport(
        csv_path=csv_path,
        csv_rows=len(rows),
        folders_with_images=len(scan.images),
        total_images=sum(len(v) for v in scan.images.values()),
        empty_folders=len(scan.empty),
        images_without_csv=build.images_without_csv,
        csv_without_images=build.csv_without_images,
        warnings=scan.warnings + csv_warnings,
    )
    for record in build.records:
        try:
            valid.append(schemas.ProductBase(**record))
        except ValidationError as exc:
            reasons = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            report.invalid.append(f"{record['sku']}: {reasons}")

    db = SessionLocal()
    try:
        fid = factory_id or settings.DEFAULT_FACTORY_ID
        factory = db.query(models.Factory).filter(models.Factory.id == fid).first() if fid else None
        if not factory:
            names = ", ".join(f"{f.id}: {f.name}" for f in db.query(models.Factory).order_by(models.Factory.id))
            head = f"لا يوجد مصنع برقم {fid}" if fid else "لم يُحدَّد المصنع (DEFAULT_FACTORY_ID في .env)"
            raise ImportConfigError(f"{head}. المصانع المتاحة: {names or 'لا يوجد أي مصنع'}")

        plan = []
        for data in valid:
            fields = data.model_dump(include={"sku", *COMPARED_FIELDS})
            existing = db.query(models.Product).filter(models.Product.sku == data.sku).first()
            if existing is None:
                report.created.append(data.sku)
            elif existing.factory_id != factory.id:
                report.foreign.append(f"{data.sku}: يتبع مصنعاً آخر (رقم {existing.factory_id})")
                continue
            elif any(getattr(existing, k) != fields[k] for k in COMPARED_FIELDS):
                report.updated.append(data.sku)
            else:
                report.unchanged.append(data.sku)
                continue
            plan.append(fields)

        if apply and plan:
            report.backup_path = backup_sqlite()
            try:
                touched = [upsert_product(db, factory, fields)[0] for fields in plan]
                db.commit()
            except Exception:
                db.rollback()
                raise
            report.touched_ids = [p.id for p in touched]
            report.applied = True
        return report
    finally:
        db.close()


async def sync_touched(product_ids: List[int]) -> int:
    """يزامن المنتجات مع ميتا ويُعيد عدد ما فشلت مزامنته."""
    await sync_products_in_background(product_ids)
    db = SessionLocal()
    try:
        return db.query(models.Product).filter(
            models.Product.id.in_(product_ids),
            models.Product.sync_status == models.SyncStatus.FAILED,
        ).count()
    finally:
        db.close()
