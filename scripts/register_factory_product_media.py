"""Register factory product image URLs in the factory media library.

Run from the project root:
    python -m scripts.register_factory_product_media          # preview only
    python -m scripts.register_factory_product_media --apply  # save to database
"""
import argparse
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.core.database import SessionLocal
from app.services.factories import get_factory

FACTORY_ID = 4


def register_product_images(
    db: Session, *, factory_id: int = FACTORY_ID, apply: bool = False,
) -> dict[str, Any]:
    get_factory(db, factory_id)
    products = db.scalars(
        select(models.Product)
        .where(models.Product.factory_id == factory_id)
        .order_by(models.Product.id)
    ).all()
    existing_urls = set(db.scalars(
        select(models.Media.url).where(models.Media.factory_id == factory_id)
    ).all())
    seen_urls = set(existing_urls)
    summary: dict[str, Any] = {
        "products": len(products),
        "image_urls_found": 0,
        "added": [],
        "already_registered": 0,
        "duplicate_product_urls": 0,
    }

    for product in products:
        urls = [product.primary_media_url]
        if isinstance(product.additional_media_urls, list):
            urls.extend(product.additional_media_urls)

        for raw_url in urls:
            if not isinstance(raw_url, str) or not raw_url.strip():
                continue
            url = raw_url.strip()
            summary["image_urls_found"] += 1
            if url in seen_urls:
                if url in existing_urls:
                    summary["already_registered"] += 1
                else:
                    summary["duplicate_product_urls"] += 1
                continue

            seen_urls.add(url)
            summary["added"].append((url, product.sku, product.title))
            if apply:
                db.add(models.Media(
                    factory_id=factory_id,
                    media_type="image",
                    url=url,
                    description=f"{product.sku} - {product.title}",
                ))

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
        description="تسجيل روابط صور منتجات المصنع 4 في مكتبة الوسائط",
    )
    parser.add_argument(
        "--apply", action="store_true", help="احفظ الروابط فعلياً (بدونه: معاينة فقط)",
    )
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        summary = register_product_images(db, apply=args.apply)
    finally:
        db.close()

    mode = "تم الحفظ" if args.apply else "معاينة فقط"
    print(
        f"{mode}: {summary['products']} منتجاً، "
        f"{summary['image_urls_found']} رابط صورة؛ "
        f"{len(summary['added'])} جديد، "
        f"{summary['already_registered']} مسجل مسبقاً، "
        f"{summary['duplicate_product_urls']} مكرر بين المنتجات."
    )
    for url, sku, title in summary["added"]:
        print(f"  {sku} - {title}: {url}")
    if not args.apply and summary["added"]:
        print("للتسجيل أعد التشغيل مع --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
