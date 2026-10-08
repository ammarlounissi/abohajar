"""استيراد المنتجات تلقائياً من مجلد الصور وملف CSV.

التشغيل من جذر المشروع (والبيئة الافتراضية مفعّلة):

    python -m scripts.import_from_media                  # تجربة فقط، لا تكتب شيئاً
    python -m scripts.import_from_media --apply          # كتابة فعلية
    python -m scripts.import_from_media --apply --sync   # ومزامنة ميتا بعدها

المسارات والمصنع من .env (PRODUCTS_CSV_PATH وDEFAULT_FACTORY_ID)، ويمكن تجاوزهما
مؤقتاً بالخيارين --csv و--factory-id.

ما يفعله:
  1. يفحص MEDIA_ROOT: لكل مجلد SKU فيه صور مرقمة يستنتج روابطها.
  2. يقرأ العنوان والسعر من CSV (الأعمدة sku,title,price).
  3. يضيف المنتج أو يحدّث (العنوان، السعر، الصورة الرئيسية، الصور الإضافية) عند تغيّرها فقط.
لا يحذف أي منتج، ولا يمسّ الوصف ولا بيانات المنتجات التي لا صور لها.
"""
import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from pydantic import ValidationError

from app import models, schemas
from app.core.config import settings
from app.core.database import SessionLocal
from app.services.media_scan import build_records, read_csv_rows, scan_media_root
from app.services.meta import sync_products_in_background
from app.services.products import upsert_product

COMPARED_FIELDS = ("title", "price", "primary_media_url", "additional_media_urls")


def _show(title: str, items: List[str], limit: int, verbose: bool) -> None:
    if not items:
        return
    print(f"\n{title} ({len(items)}):")
    shown = items if verbose else items[:limit]
    for item in shown:
        print(f"   - {item}")
    if len(shown) < len(items):
        print(f"   ... و{len(items) - len(shown)} أخرى (استعمل --verbose لعرضها كلها)")


def _backup_sqlite() -> None:
    """نسخة احتياطية بختم زمني قبل أي كتابة. نستعمل واجهة النسخ في sqlite3 لأن القاعدة بوضع WAL."""
    url = settings.DATABASE_URL
    if not url.startswith("sqlite:///"):
        print("⚠️ القاعدة ليست SQLite: لم تُؤخذ نسخة احتياطية تلقائياً.")
        return
    src_path = Path(url[len("sqlite:///"):])
    if not src_path.is_file():
        return
    dest_path = src_path.with_name(f"{src_path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    src, dest = sqlite3.connect(src_path), sqlite3.connect(dest_path)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    print(f"💾 نسخة احتياطية: {dest_path}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="استيراد المنتجات من مجلد الصور وCSV")
    parser.add_argument("--factory-id", type=int, help="المصنع الذي تُنسب إليه المنتجات الجديدة (الافتراضي: DEFAULT_FACTORY_ID)")
    parser.add_argument("--csv", type=Path, help="مسار ملف CSV (الافتراضي: PRODUCTS_CSV_PATH ثم MEDIA_ROOT/products.csv)")
    parser.add_argument("--apply", action="store_true", help="اكتب في القاعدة فعلاً (بدونه: تجربة فقط)")
    parser.add_argument("--sync", action="store_true", help="زامن المنتجات المضافة/المعدّلة مع ميتا بعد الكتابة")
    parser.add_argument("--verbose", action="store_true", help="اعرض كل القوائم كاملة")
    args = parser.parse_args(argv)

    settings.validate()
    csv_path = args.csv or settings.PRODUCTS_CSV_PATH or settings.MEDIA_ROOT / "products.csv"
    if not csv_path.is_file():
        print(f"❌ ملف CSV غير موجود: {csv_path}")
        return 2

    scan = scan_media_root(settings.MEDIA_ROOT)
    try:
        rows, csv_warnings = read_csv_rows(csv_path)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 2
    build = build_records(scan, rows)

    # نتحقق من كل سجل بنفس مخطط الـ API (السعر > 0، العنوان، الرمز...)
    valid, invalid = [], []
    for record in build.records:
        try:
            valid.append(schemas.ProductBase(**record))
        except ValidationError as exc:
            reasons = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
            invalid.append(f"{record['sku']}: {reasons}")

    db = SessionLocal()
    try:
        factory_id = args.factory_id or settings.DEFAULT_FACTORY_ID
        factory = (
            db.query(models.Factory).filter(models.Factory.id == factory_id).first()
            if factory_id else None
        )
        if not factory:
            if factory_id:
                print(f"❌ لا يوجد مصنع برقم {factory_id}. المصانع المتاحة:")
            else:
                print("❌ لم يُحدَّد المصنع: ضع DEFAULT_FACTORY_ID في .env أو مرّر --factory-id. المصانع المتاحة:")
            for f in db.query(models.Factory).order_by(models.Factory.id):
                print(f"   {f.id}: {f.name}")
            return 2

        created, updated, unchanged, foreign = [], [], [], []
        plan = []
        for data in valid:
            fields = data.model_dump(include={"sku", *COMPARED_FIELDS})
            existing = db.query(models.Product).filter(models.Product.sku == data.sku).first()
            if existing is None:
                created.append(data.sku)
            elif existing.factory_id != factory.id:
                foreign.append(f"{data.sku}: يتبع مصنعاً آخر (رقم {existing.factory_id})")
                continue
            elif any(getattr(existing, k) != fields[k] for k in COMPARED_FIELDS):
                updated.append(data.sku)
            else:
                unchanged.append(data.sku)
                continue
            plan.append(fields)

        total_images = sum(len(v) for v in scan.images.values())
        print(f"📁 المجلد: {settings.MEDIA_ROOT}")
        print(f"📄 CSV: {csv_path} ({len(rows)} منتجاً)")
        print(f"🖼️ مجلدات فيها صور: {len(scan.images)} (إجمالي {total_images} صورة)، مجلدات فارغة: {len(scan.empty)}")
        print(f"\n➕ جديد: {len(created)}   ✏️ تحديث: {len(updated)}   ⏸️ بلا تغيير: {len(unchanged)}")

        _show("⚠️ صور بلا سطر في CSV (لن تُضاف، ينقصها العنوان والسعر)", build.images_without_csv, 15, args.verbose)
        _show("⚠️ في CSV بلا صور (لن تُضاف، الصورة إلزامية)", build.csv_without_images, 15, args.verbose)
        _show("❌ بيانات غير صالحة", invalid, 15, args.verbose)
        _show("❌ تعارض ملكية", foreign, 15, args.verbose)
        _show("ℹ️ تنبيهات الفحص", scan.warnings + csv_warnings, 15, args.verbose)

        if not args.apply:
            print("\n🔎 تجربة فقط، لم يُكتب شيء. أضف --apply للتنفيذ.")
            return 0
        if not plan:
            print("\nلا شيء يحتاج كتابة.")
            return 0

        _backup_sqlite()
        touched = []
        try:
            for fields in plan:
                product, _ = upsert_product(db, factory, fields)
                touched.append(product)
            db.commit()
        except Exception:
            db.rollback()
            raise
        ids = [p.id for p in touched]
        print(f"\n✅ تمت الكتابة: {len(created)} جديد، {len(updated)} محدّث.")

        if args.sync:
            print(f"🔄 مزامنة {len(ids)} منتجاً مع ميتا (قد تستغرق وقتاً)...")
            asyncio.run(sync_products_in_background(ids))
            failed = db.query(models.Product).filter(
                models.Product.id.in_(ids), models.Product.sync_status == models.SyncStatus.FAILED
            ).count()
            print(f"   انتهت المزامنة، الفاشل منها: {failed}")
        else:
            print("ℹ️ المنتجات الجديدة/المعدّلة حالتها pending، شغّل الأمر مع --sync لمزامنتها.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
