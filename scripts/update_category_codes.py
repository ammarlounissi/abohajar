"""Set each product category_code from the leading letters of its SKU.

Run from the project root:
    python -m scripts.update_category_codes                 # preview only
    python -m scripts.update_category_codes --apply         # save updates
"""
import argparse
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.core.database import SessionLocal
from app.services.media import InvalidSkuError, category_of


def update_category_codes(db: Session, *, apply: bool = False) -> dict[str, Any]:
    products = db.scalars(select(models.Product).order_by(models.Product.id)).all()
    summary: dict[str, Any] = {
        "updated": [],
        "unchanged": 0,
        "invalid": [],
    }

    for product in products:
        try:
            category_code = category_of(product.sku)
        except InvalidSkuError:
            summary["invalid"].append(product.sku)
            continue
        if product.category_code == category_code:
            summary["unchanged"] += 1
            continue
        summary["updated"].append((product.sku, product.category_code, category_code))
        if apply:
            product.category_code = category_code

    if apply:
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
    else:
        db.rollback()
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="استخراج category_code من الحروف الأولى لرمز SKU",
    )
    parser.add_argument(
        "--apply", action="store_true", help="احفظ التعديلات فعلياً (بدونه: معاينة فقط)",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        summary = update_category_codes(db, apply=args.apply)
    finally:
        db.close()

    mode = "تم الحفظ" if args.apply else "معاينة فقط"
    print(f"{mode}: {len(summary['updated'])} منتجاً يحتاج تحديثاً؛ "
          f"{summary['unchanged']} دون تغيير؛ {len(summary['invalid'])} SKU غير صالح.")
    for sku, old, new in summary["updated"]:
        print(f"  {sku}: {old} -> {new}")
    for sku in summary["invalid"]:
        print(f"  SKU غير صالح، تم تخطيه: {sku!r}", file=sys.stderr)
    if not args.apply and summary["updated"]:
        print("للحفظ أعد التشغيل مع --apply.")
    return 1 if summary["invalid"] else 0


if __name__ == "__main__":
    sys.exit(main())
