from fastapi import FastAPI, Depends, HTTPException, status, Query, Response, Body, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from typing import List
import os
import io
import pandas as pd
from dotenv import load_dotenv

import models, schemas
from database import engine, get_db, SessionLocal
from meta_service import sync_product_to_meta
from whatsapp_service import send_whatsapp_message, download_and_save_whatsapp_media

# تحميل متغيرات البيئة من ملف .env
load_dotenv()

app = FastAPI(title="Hojrat Bladi API", version="1.1.0")

# إتاحة الوصول للملفات الثابتة عبر الويب
os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# تفعيل CORS للتطبيقات والواجهات الخارجية
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# جلب رمز التحقق من ملف .env مباشرة بدون قيمة افتراضية
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")

# قائمة عبارات السلام والتحية
GREETING_KEYWORDS = [
    "السلام عليكم",
    "سلام عليكم",
    "السلام",
    "سلام",
    "مرحبا",
    "صباح الخير",
    "مساء الخير",
    "salam",
    "slm"
]

def check_greeting(text: str) -> bool:
    """التحقق مما إذا كانت الرسالة تحتوي على عبارة سلام"""
    clean_text = text.lower().strip()
    return any(keyword in clean_text for keyword in GREETING_KEYWORDS)


# --- Endpoints المصانع ---
@app.post("/api/factories/", response_model=schemas.FactoryResponse, status_code=status.HTTP_201_CREATED)
def create_factory(factory: schemas.FactoryCreate, db: Session = Depends(get_db)):
    db_factory = db.query(models.Factory).filter(models.Factory.phone_number == factory.phone_number).first()
    if db_factory:
        raise HTTPException(status_code=400, detail="رقم الهاتف مسجل بالفعل لمصنع آخر")
    new_factory = models.Factory(**factory.dict())
    db.add(new_factory)
    db.commit()
    db.refresh(new_factory)
    return new_factory

@app.get("/api/factories/", response_model=List[schemas.FactoryResponse])
def get_factories(db: Session = Depends(get_db)):
    return db.query(models.Factory).all()


# --- Endpoints المنتجات ---

# 1. إضافة منتج واحد فردياً (API)
@app.post("/api/products/", response_model=schemas.ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(product: schemas.ProductCreate, db: Session = Depends(get_db)):
    factory = db.query(models.Factory).filter(models.Factory.id == product.factory_id).first()
    if not factory:
        raise HTTPException(status_code=404, detail="المصنع غير موجود")
    
    db_product = db.query(models.Product).filter(models.Product.sku == product.sku).first()
    if db_product:
        raise HTTPException(status_code=400, detail="رمز SKU مستخدم مسبقاً")
    
    new_product = models.Product(**product.dict())
    db.add(new_product)
    db.commit()
    db.refresh(new_product)
    return new_product

@app.get("/api/products/", response_model=List[schemas.ProductResponse])
def get_products(db: Session = Depends(get_db)):
    return db.query(models.Product).all()

@app.post("/api/products/{product_id}/sync-meta")
async def trigger_meta_sync(product_id: int, db: Session = Depends(get_db)):
    result = await sync_product_to_meta(product_id, db)
    return result


# 2. رفع وتحديث منتجات كثيرة عبر ملف CSV (مع التحديث الذكي للحقول المتغيرة)
@app.post("/api/products/upload-csv", status_code=status.HTTP_200_OK)
async def upload_products_csv(
    factory_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    """
    رفع ملف CSV يحتوي على المنتجات.
    - إضافة المنتجات الجديدة التي لا تملاك رمز SKU في النظام.
    - تحديث المنتجات المتاحة مسبقاً في حالة تغير البيانات المتغيرة (مثل السعر، العنوان، الصورة).
    """
    factory = db.query(models.Factory).filter(models.Factory.id == factory_id).first()
    if not factory:
        raise HTTPException(status_code=404, detail="المصنع غير موجود")

    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="يرجى رفع ملف بصيغة CSV فقط")

    try:
        content = await file.read()
        df = pd.read_csv(io.BytesIO(content))

        # توحيد أسماء الترويسات (حروف صغيرة وبدون فراغات)
        df.columns = df.columns.str.strip().str.lower()

        # الأعمدة الإلزامية الأساسية
        required_columns = {"sku", "title", "price", "primary_media_url"}
        missing_columns = required_columns - set(df.columns)
        if missing_columns:
            raise HTTPException(
                status_code=400,
                detail=f"الملف ينقصه الأعمدة الإلزامية التالية: {', '.join(missing_columns)}"
            )

        created_count = 0
        updated_count = 0
        unchanged_count = 0
        processed_products = []
        errors = []

        for index, row in df.iterrows():
            row_num = index + 2  # رقم السطر مع ترويسة الجدول

            sku = str(row["sku"]).strip().upper() if pd.notna(row.get("sku")) else None
            title = str(row["title"]).strip() if pd.notna(row.get("title")) else None
            primary_url = str(row["primary_media_url"]).strip() if pd.notna(row.get("primary_media_url")) else None

            if not sku or not title or not primary_url:
                errors.append(f"السطر {row_num}: أحد الحقول الإلزامية (sku, title, primary_media_url) مفقود")
                continue

            try:
                price = float(row["price"])
            except (ValueError, TypeError):
                errors.append(f"السطر {row_num}: قيمة السعر غير صالحة ({row.get('price')})")
                continue

            # الحقول الاختيارية والافتراضية
            description = str(row["description"]).strip() if "description" in df.columns and pd.notna(row["description"]) else f"{title} - توريد مباشر من مصنع {factory.name}"
            currency = str(row["currency"]).strip() if "currency" in df.columns and pd.notna(row["currency"]) else "DZD"
            condition = str(row["condition"]).strip() if "condition" in df.columns and pd.notna(row["condition"]) else "new"
            brand = str(row["brand"]).strip() if "brand" in df.columns and pd.notna(row["brand"]) else "Hojrat Bladi"

            additional_urls = []
            if "additional_media_urls" in df.columns and pd.notna(row["additional_media_urls"]):
                additional_urls = [u.strip() for u in str(row["additional_media_urls"]).split("|") if u.strip()]

            # فحص وجود المنتج بناءً على SKU
            existing_product = db.query(models.Product).filter(models.Product.sku == sku).first()

            if existing_product:
                # التحقق من وجود تغييرات في البيانات لتحديث المتغيرات فقط
                has_changes = False

                if existing_product.title != title:
                    existing_product.title = title
                    has_changes = True

                if existing_product.price != price:
                    existing_product.price = price
                    has_changes = True

                if existing_product.primary_media_url != primary_url:
                    existing_product.primary_media_url = primary_url
                    has_changes = True

                if existing_product.description != description:
                    existing_product.description = description
                    has_changes = True

                if existing_product.currency != currency:
                    existing_product.currency = currency
                    has_changes = True

                if existing_product.additional_media_urls != additional_urls:
                    existing_product.additional_media_urls = additional_urls
                    has_changes = True

                if has_changes:
                    existing_product.sync_status = models.SyncStatus.PENDING
                    updated_count += 1
                    processed_products.append(existing_product)
                else:
                    unchanged_count += 1
            else:
                # إنشاء منتج جديد
                new_product = models.Product(
                    sku=sku,
                    title=title,
                    description=description,
                    price=price,
                    currency=currency,
                    availability=models.Availability.IN_STOCK,
                    condition=condition,
                    brand=brand,
                    primary_media_url=primary_url,
                    additional_media_urls=additional_urls,
                    factory_id=factory.id
                )
                db.add(new_product)
                created_count += 1
                processed_products.append(new_product)

        # حفظ جميع العمليات في قاعدة البيانات
        db.commit()

        # إعادة المزامنة مع Meta للعلامات التي تمت إضافتها أو تحديثها
        for product in processed_products:
            db.refresh(product)
            await sync_product_to_meta(product.id, db)

        return {
            "status": "success",
            "message": f"تمت معالجة ملف CSV بنجاح.",
            "summary": {
                "total_created": created_count,
                "total_updated": updated_count,
                "total_unchanged": unchanged_count,
                "total_errors": len(errors)
            },
            "errors": errors
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"حدث خطأ أثناء معالجة ملف CSV: {str(e)}")


# --- Webhook Endpoints ---
@app.get("/webhook")
async def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge")
):
    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("WEBHOOK_VERIFIED")
            return Response(content=challenge, status_code=200)
        else:
            raise HTTPException(status_code=403, detail="Verification token mismatch")
    raise HTTPException(status_code=400, detail="Missing parameters")


@app.post("/webhook")
async def handle_whatsapp_messages(payload: dict = Body(...)):
    """استقبال رسائل الواتساب، حفظ الوسائط وإضافة منتج فردي عبر الصورة"""
    data = payload
    print("Received Webhook Event:", data)

    try:
        entry = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})

        if "messages" in entry:
            message = entry["messages"][0]
            from_phone = message["from"]
            msg_type = message.get("type")

            db = SessionLocal()
            try:
                # 1. التحقق من هوية المصنع
                factory = db.query(models.Factory).filter(
                    (models.Factory.phone_number == from_phone) |
                    (models.Factory.phone_number == f"+{from_phone}")
                ).first()

                if not factory:
                    await send_whatsapp_message(
                        from_phone,
                        "⚠️ مرحباً بك! هذا الرقم غير مسجل كمصنع معتمد في منصة حجرة بلادي."
                    )
                    return {"status": "unauthorized_factory"}

                # 2. معالجة الرسائل النصية
                if msg_type == "text":
                    raw_text = message.get("text", {}).get("body", "").strip()

                    if check_greeting(raw_text):
                        reply = (
                            "وعليكم السلام ورحمة الله وبركاته 🌸\n"
                            f"أهلاً بك مصنع ({factory.name}) في منصة حجرة بلادي 🏛️.\n\n"
                            "أرسل كلمة *إضافة منتج* للبدء في رفع منتجاتك."
                        )
                    elif "إضافة منتج" in raw_text or "اضافة منتج" in raw_text:
                        reply = (
                            f"أهلاً بك مصنع ({factory.name}) 🏛️\n\n"
                            "لإضافة منتج جديد، أرسل *صورة الحجر* واكتب في الشرح (Caption) التنسيق التالي:\n\n"
                            "*اسم المنتج - السعر - رمز SKU*\n\n"
                            "📌 مثال:\n"
                            "حجر قالمة بيج - 3200 - STONE-GLM-01"
                        )
                    else:
                        reply = "مرحباً بك! أرسل كلمة *إضافة منتج* للبدء في رفع منتجاتك."

                    await send_whatsapp_message(from_phone, reply)

                # 3. معالجة رفع منتج واحد عبر الصورة (تنزيل الصورة وحفظها في مجلد المصنع)
                elif msg_type == "image":
                    caption = message.get("image", {}).get("caption", "").strip()
                    media_id = message.get("image", {}).get("id")

                    if not caption or "-" not in caption:
                        await send_whatsapp_message(
                            from_phone,
                            "⚠️ يرجى كتابة تفاصيل المنتج مع الصورة مفصولة بـ (-) مثال:\n"
                            "*حجر قالمة بيج - 3200 - STONE-01*"
                        )
                        return {"status": "invalid_format"}

                    parts = [p.strip() for p in caption.split("-") if p.strip()]
                    if len(parts) < 3:
                        await send_whatsapp_message(
                            from_phone,
                            "⚠️ بيانات ناقصة. تأكد من كتابة: *الاسم - السعر - رمز SKU*"
                        )
                        return {"status": "missing_fields"}

                    price = None
                    sku = None
                    title_parts = []

                    for part in parts:
                        if price is None and (part.isdigit() or part.replace(".", "", 1).isdigit()):
                            price = float(part)
                        elif sku is None and any(c.isascii() and c.isalpha() for c in part):
                            sku = part.upper()
                        else:
                            title_parts.append(part)

                    if not title_parts:
                        title_parts.append(parts[0])
                    if not sku:
                        sku = parts[-1].upper()
                    if price is None:
                        try:
                            price = float(parts[1])
                        except ValueError:
                            price = 0.0

                    title = " - ".join(title_parts)

                    # فحص تكرار الـ SKU
                    existing_product = db.query(models.Product).filter(models.Product.sku == sku).first()
                    if existing_product:
                        await send_whatsapp_message(from_phone, f"⚠️ رمز المنتج ({sku}) مستخدم مسبقاً، يرجى اختيار رمز آخر.")
                        return {"status": "sku_exists"}

                    # تحميل الصورة وحفظها داخل مجلد المصنع محلياً
                    image_url = await download_and_save_whatsapp_media(media_id, factory_id=factory.id, media_type="image")
                    if not image_url:
                        image_url = "https://images.unsplash.com/photo-1590381105924-c72589b9ef3f"

                    new_product = models.Product(
                        sku=sku,
                        title=title,
                        description=f"{title} - توريد مباشر من مصنع {factory.name}",
                        price=price,
                        currency="DZD",
                        availability=models.Availability.IN_STOCK,
                        condition="new",
                        brand="Hojrat Bladi",
                        primary_media_url=image_url,
                        factory_id=factory.id
                    )
                    db.add(new_product)
                    db.commit()
                    db.refresh(new_product)

                    # المزامنة مع Meta Commerce
                    sync_res = await sync_product_to_meta(new_product.id, db)

                    if sync_res.get("status") == "success":
                        await send_whatsapp_message(
                            from_phone,
                            f"✅ تم إضافة ومزامنة المنتج بنجاح!\n\n"
                            f"📦 المنتج: {title}\n"
                            f"💰 السعر: {price} دج\n"
                            f"🏷️ الرمز: {sku}\n"
                            f"🆔 معرّف ميتا: {new_product.meta_product_id}"
                        )
                    else:
                        await send_whatsapp_message(
                            from_phone,
                            f"✅ تم حفظ المنتج محلياً برقم ({new_product.id})."
                        )

            finally:
                db.close()

    except Exception as e:
        print(f"Error handling WhatsApp webhook: {e}")

    return {"status": "success"}


# ربط المجلد بالمصار /products
app.mount("/products", StaticFiles(directory="products"), name="products")


@app.delete("/api/products/clear-all")
def clear_all_products_endpoint(db: Session = Depends(get_db)):
    try:
        num_deleted = db.query(models.Product).delete(synchronize_session=False)
        db.commit()
        return {"status": "success", "message": f"تم حذف {num_deleted} منتج بنجاح."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"خطأ أثناء الحذف: {str(e)}")