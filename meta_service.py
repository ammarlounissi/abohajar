import httpx
import os
from dotenv import load_dotenv
from sqlalchemy.orm import Session
import models

load_dotenv()

async def sync_product_to_meta(product_id: int, db: Session):
    access_token = os.getenv("META_ACCESS_TOKEN")
    catalog_id = os.getenv("META_CATALOG_ID")

    if not access_token or not catalog_id:
        return {
            "status": "error", 
            "message": "لم يتم العثور على META_ACCESS_TOKEN أو META_CATALOG_ID في ملف .env"
        }

    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        return {"status": "error", "message": f"المنتج رقم {product_id} غير موجود في قاعدة البيانات"}

    graph_url = f"https://graph.facebook.com/v19.0/{catalog_id}/products"
    
    # تحويل السعر إلى سنتات أو رقم مباشر حسب متطلبات ميتا
    # إرسال السعر كقيمة رقمية فقط وإضافة حقل currency منفصل
    price_in_cents = int(product.price * 100) # تحويل للدينار بـ 100 سنت إذا لزم الأمر أو تمرير الرقم المباشر

    meta_payload = {
        "retailer_id": str(product.sku),
        "name": str(product.title),
        "description": str(product.description or product.title),
        "availability": "in stock",
        "condition": "new",
        "price": price_in_cents,   # إرسال الرقم (مثل 350000 للسنتات)
        "currency": "DZD",         # حقل العملة المطلوب الإجباري
        "url": str(product.primary_media_url),
        "image_url": str(product.primary_media_url),
        "brand": str(product.brand or "Hojrat Bladi")
    }

    headers = {
        "Authorization": f"Bearer {access_token.strip()}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(graph_url, json=meta_payload, headers=headers)
            res_data = response.json()

            if response.status_code == 200 and "id" in res_data:
                product.meta_product_id = res_data["id"]
                product.sync_status = models.SyncStatus.SYNCED
                product.sync_error_message = None
                db.commit()
                return {"status": "success", "meta_product_id": res_data["id"]}
            else:
                error_msg = res_data.get("error", {}).get("message", str(res_data))
                product.sync_status = models.SyncStatus.FAILED
                product.sync_error_message = str(error_msg)
                db.commit()
                return {"status": "failed", "error": error_msg}

    except Exception as e:
        return {"status": "error", "message": f"خطأ أثناء الاتصال: {str(e)}"}