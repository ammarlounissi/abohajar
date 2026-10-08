"""استيراد المنتجات تلقائياً من مجلد الصور وملف CSV.

التشغيل من جذر المشروع (والبيئة الافتراضية مفعّلة):

    python -m scripts.import_from_media                  # تجربة فقط، لا تكتب شيئاً
    python -m scripts.import_from_media --apply          # كتابة فعلية
    python -m scripts.import_from_media --apply --sync   # ومزامنة ميتا بعدها

المسارات والمصنع من .env (PRODUCTS_CSV_PATH وDEFAULT_FACTORY_ID)، ويمكن تجاوزهما
مؤقتاً بالخيارين --csv و--factory-id. المنطق نفسه في app/services/media_import.py
وهو ما يشغّله أمر واتساب الإداري.
"""
import argparse
import asyncio
import sys
from pathlib import Path

from app.core.config import settings
from app.services.import_report import ImportConfigError, format_report
from app.services.media_import import run_import, sync_touched


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="استيراد المنتجات من مجلد الصور وCSV")
    parser.add_argument("--factory-id", type=int, help="المصنع الذي تُنسب إليه المنتجات الجديدة (الافتراضي: DEFAULT_FACTORY_ID)")
    parser.add_argument("--csv", type=Path, help="مسار ملف CSV (الافتراضي: PRODUCTS_CSV_PATH ثم MEDIA_ROOT/products.csv)")
    parser.add_argument("--apply", action="store_true", help="اكتب في القاعدة فعلاً (بدونه: تجربة فقط)")
    parser.add_argument("--sync", action="store_true", help="زامن المنتجات المضافة/المعدّلة مع ميتا بعد الكتابة")
    parser.add_argument("--verbose", action="store_true", help="اعرض كل القوائم كاملة")
    args = parser.parse_args(argv)

    settings.validate()
    try:
        report = run_import(apply=args.apply, factory_id=args.factory_id, csv_path=args.csv)
    except ImportConfigError as exc:
        print(f"❌ {exc}")
        return 2

    print(f"📁 المجلد: {settings.MEDIA_ROOT}")
    print(format_report(report, limit=None if args.verbose else 15))

    if not args.apply:
        print("\n🔎 تجربة فقط، لم يُكتب شيء. أضف --apply للتنفيذ.")
        return 0
    if not report.applied:
        print("\nلا شيء يحتاج كتابة.")
        return 0

    if args.sync:
        print(f"\n🔄 مزامنة {len(report.touched_ids)} منتجاً مع ميتا (قد تستغرق وقتاً)...")
        failed = asyncio.run(sync_touched(report.touched_ids))
        print(f"   انتهت المزامنة، الفاشل منها: {failed}")
    else:
        print("\nℹ️ المنتجات الجديدة/المعدّلة حالتها pending، شغّل الأمر مع --sync لمزامنتها.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
