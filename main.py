from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from meta_service import sync_product_to_meta
from fastapi import Request, Query, Response
import models, schemas
from database import engine, get_db
from json import JSONDecodeError
from whatsapp_service import send_whatsapp_message
from fastapi.middleware.cors import CORSMiddleware
import models
from database import SessionLocal
from meta_service import sync_product_to_meta
from whatsapp_service import send_whatsapp_message, get_media_url

app = FastAPI(title="Hojrat Bladi API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# --- Endpoints المصانع ---

# رمز التحقق الخاص بك (اختر أي كلمة سرية وضِعها هنا وفي لوحة Meta)
VERIFY_TOKEN = "HOJRAT_BLADI_WEBHOOK_TOKEN_2026"


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

# --- Endpoints المنتجات (التي ستُغذى من البوت ومن واجهة Next.js) ---

@app.post("/api/products/", response_model=schemas.ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(product: schemas.ProductCreate, db: Session = Depends(get_db)):
    # التأكد من وجود المصنع
    factory = db.query(models.Factory).filter(models.Factory.id == product.factory_id).first()
    if not factory:
        raise HTTPException(status_code=404, detail="المصنع غير موجود")
    
    # التأكد من عدم تكرار الـ SKU
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
    """هذا الـ Endpoint سيعتمد عليه تطبيق Next.js لعرض التغذية البصرية (Feed)"""
    return db.query(models.Product).all()

@app.post("/api/products/{product_id}/sync-meta")
async def trigger_meta_sync(product_id: int, db: Session = Depends(get_db)):
    """
    مزامنة منتج معين مع Ktalog Meta يدوياً
    """
    result = await sync_product_to_meta(product_id, db)
    return result


@app.get("/webhook")
async def verify_webhook(
    mode: str = Query(None, alias="hub.mode"),
    token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge")
):
    """
    Endpoint للتحقق التلقائي الذي تطلبه Meta عند ضبط الـ Webhook لأول مرة
    """
    if mode and token:
        if mode == "subscribe" and token == VERIFY_TOKEN:
            print("WEBHOOK_VERIFIED")
            return Response(content=challenge, status_code=200)
        else:
            raise HTTPException(status_code=403, detail="Verification token mismatch")
    raise HTTPException(status_code=400, detail="Missing parameters")



@app.post("/webhook")
async def handle_whatsapp_messages(request: Request):
    try:
        data = await request.json()
    except JSONDecodeError:
        return {"status": "ignored", "reason": "empty_body"}

    try:
        entry = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        
        if "messages" in entry:
            message = entry["messages"][0]
            from_phone = message["from"]  # رقم المرسل
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

                # 2. في حالة الرسائل النصية
                if msg_type == "text":
                    text = message["text"]["body"].strip()
                    if text in ["إضافة منتج", "اضافة منتج"]:
                        reply = (
                            f"أهلاً بك مصنع ({factory.name}) 🏛️\n\n"
                            "لإضافة منتج جديد، أرسل *صورة الحجر* واكتب في الشرح (Caption) التنسيق التالي:\n\n"
                            "*اسم المنتج - السعر - رمز SKU*\n\n"
                            "📌 مثال:\n"
                            "حجر قالمة بيج - 3200 - STONE-GLM-01"
                        )
                    else:
                        reply = "مرحباً بك! أرسل كلمة *إضافة منتج* للبدء في رفع منتج جديد."
                    
                    await send_whatsapp_message(from_phone, reply)

                # 3. في حالة إرسال صورة مع شرح (Caption)
                elif msg_type == "image":
                    caption = message.get("image", {}).get("caption", "").strip()
                    media_id = message.get("image", {}).get("id")

                    if not caption or "-" not in caption:
                        await send_whatsapp_message(
                            from_phone,
                            "⚠️ يرجى إرفاق تفاصيل المنتج مع الصورة بالتنسيق التالي:\n"
                            "*اسم المنتج - السعر - رمز SKU*"
                        )
                        return {"status": "invalid_format"}

                    # تفكيك النص: الاسم - السعر - SKU
                    parts = [p.strip() for p in caption.split("-")]
                    if len(parts) < 3:
                        await send_whatsapp_message(
                            from_phone,
                            "⚠️ بيانات ناقصة. تأكد من كتابة: *الاسم - السعر - رمز SKU*"
                        )
                        return {"status": "missing_fields"}

                    title = parts[0]
                    try:
                        price = float(parts[1])
                    except ValueError:
                        await send_whatsapp_message(from_phone, "⚠️ يرجى التأكد من كتابة السعر كأرقام فقط.")
                        return {"status": "invalid_price"}
                    
                    sku = parts[2]

                    # التحقق من عدم تكرار الـ SKU
                    existing_product = db.query(models.Product).filter(models.Product.sku == sku).first()
                    if existing_product:
                        await send_whatsapp_message(from_phone, f"⚠️ رمز المنتج ({sku}) مستخدم مسبقاً، يرجى اختيار رمز آخر.")
                        return {"status": "sku_exists"}

                    # جلب رابط الصورة (أو رابط تجريبي إذا كنا في بيئة التطوير)
                    image_url = await get_media_url(media_id)
                    if not image_url:
                        image_url = "https://images.unsplash.com/photo-1590381105924-c72589b9ef3f"

                    # إنشاء المنتج في قاعدة البيانات
                    new_product = models.Product(
                        sku=sku,
                        title=title,
                        description=f"{title} - توريد مباشر من مصنع {factory.name}",
                        price=price,
                        currency="DZD",
                        availability=models.AvailabilityEnum.IN_STOCK,
                        condition=models.ConditionEnum.NEW,
                        brand="Hojrat Bladi",
                        primary_media_url=image_url,
                        factory_id=factory.id
                    )
                    db.add(new_product)
                    db.commit()
                    db.refresh(new_product)

                    # المزامنة مع Meta الكتالوج
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
                            f"✅ تم حفظ المنتج محلياً برقم ({new_product.id})، وجارٍ استكمال مزامنته مع الكتالوج."
                        )

            finally:
                db.close()

    except Exception as e:
        print(f"Error handling WhatsApp webhook: {e}")

    return {"status": "success"}