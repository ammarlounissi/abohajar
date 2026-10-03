import logging
from typing import Iterable

import httpx
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


def _build_payload(product: models.Product) -> dict:
    return {
        "retailer_id": product.sku,
        "name": product.title,
        "description": product.description or product.title,
        "availability": product.availability.value if product.availability else "in stock",
        "condition": product.condition or "new",
        # السعر بأصغر وحدة للعملة (كما في الكود الأصلي). تأكد منه بتجربة منتج واحد.
        "price": int(round(product.price * 100)),
        "currency": product.currency or "DZD",
        # رابط صفحة المنتج في المتجر (وليس رابط الصورة)
        "url": settings.PRODUCT_LINK_TEMPLATE.format(sku=product.sku),
        "image_url": product.primary_media_url,
        "brand": product.brand or "Hojrat Bladi",
    }


def _mark_failed(db: Session, product: models.Product, message: str) -> None:
    db.rollback()
    product.sync_status = models.SyncStatus.FAILED
    product.sync_error_message = message[:2000]
    db.commit()


async def sync_product_to_meta(product_id: int, db: Session) -> dict:
    """إنشاء المنتج في كتالوج ميتا، أو تحديثه إن كان له meta_product_id."""
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        return {"status": "error", "message": f"المنتج رقم {product_id} غير موجود"}

    payload = _build_payload(product)
    headers = {"Authorization": f"Bearer {settings.META_ACCESS_TOKEN}"}

    if product.meta_product_id:
        url = f"{settings.graph_url}/{product.meta_product_id}"
        payload.pop("retailer_id")           # التحديث لا يحتاجه
    else:
        url = f"{settings.graph_url}/{settings.META_CATALOG_ID}/products"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(url, json=payload, headers=headers)
        try:
            res_data = response.json()
        except ValueError:
            res_data = {"raw": response.text}

        if response.status_code == 200 and ("id" in res_data or res_data.get("success")):
            product.meta_product_id = res_data.get("id") or product.meta_product_id
            product.sync_status = models.SyncStatus.SYNCED
            product.sync_error_message = None
            db.commit()
            return {"status": "success", "meta_product_id": product.meta_product_id}

        error_msg = res_data.get("error", {}).get("message", str(res_data)) \
            if isinstance(res_data, dict) else str(res_data)
        logger.error("Meta sync failed for %s: %s", product.sku, error_msg)
        _mark_failed(db, product, str(error_msg))
        return {"status": "failed", "error": error_msg}

    except Exception as exc:
        logger.exception("Meta sync error for %s", product.sku)
        _mark_failed(db, product, f"خطأ أثناء الاتصال: {exc}")
        return {"status": "error", "message": str(exc)}


async def sync_products_in_background(product_ids: Iterable[int]) -> None:
    """مزامنة قائمة منتجات بجلسة قاعدة بيانات خاصة (للاستخدام داخل BackgroundTasks)."""
    db = SessionLocal()
    try:
        for pid in product_ids:
            await sync_product_to_meta(pid, db)
    finally:
        db.close()
