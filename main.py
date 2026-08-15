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
    """
    Endpoint لاستقبال رسائل الواتساب القادمة من أصحاب المصانع/الزبائن
    """
    try:
        # قراءة البيانات مع حماية السيرفر من JSON الفارغ
        data = await request.json()
    except JSONDecodeError:
        print("⚠️ Received empty or invalid JSON payload.")
        return {"status": "ignored", "reason": "empty_body"}

    print("Received Webhook Event:", data)
    
    try:
        # استخراج تفاصيل الرسالة من هيكل payload الخاص بـ Meta
        entry = data.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {})
        
        if "messages" in entry:
            message = entry["messages"][0]
            from_phone = message["from"]  # رقم صاحب المصنع أو الزبون
            
            if message["type"] == "text":
                incoming_text = message["text"]["body"].strip().lower()
                
                # الشجرة التفاعلية للبوت
                if incoming_text == "إضافة منتج":
                    reply = "أهلاً بك! يرجى إرسال صورة الحجر مرفقة بالسعر والاسم في رسالة واحدة."
                else:
                    reply = "مرحباً بك في منصة حجرة بلادي 🏛️. أرسل كلمة 'إضافة منتج' للبدء في رفع منتجاتك."
                
                # إرسال الرد التلقائي
                await send_whatsapp_message(from_phone, reply)
                
    except Exception as e:
        print(f"Error processing webhook event: {e}")
        
    return {"status": "success"}