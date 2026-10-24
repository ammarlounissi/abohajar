from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app import schemas
from app.core.database import get_db
from app.core.security import require_admin
from app.services import factories as factory_service
from app.services import video_maker
from app.services import products as product_service
from app.services.meta import sync_product_to_meta, sync_products_in_background

router = APIRouter(prefix="/api/products", tags=["products"])
admin_only = [Depends(require_admin)]


@router.post("/", response_model=schemas.ProductResponse, status_code=status.HTTP_201_CREATED, dependencies=admin_only)
def create_product(product: schemas.ProductCreate, db: Session = Depends(get_db)):
    return product_service.create_product(db, product)


@router.get("/", response_model=List[schemas.ProductPublicResponse])
def get_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    category_code: Optional[str] = Query(None, min_length=1, max_length=20),
    tag: Optional[str] = Query(None, min_length=1, max_length=50),
    db: Session = Depends(get_db),
):
    return product_service.list_products(db, skip=skip, limit=limit, category_code=category_code, tag=tag)


# قبل "/{sku}" كي لا يُفسَّر audio-files على أنه SKU
@router.get("/audio-files", response_model=List[str], dependencies=admin_only)
def list_audio_files():
    """أسماء الملفات الصوتية المتاحة للدمج (من AUDIO_ROOT)."""
    return video_maker.list_audio_files()


@router.get("/admin", response_model=List[schemas.ProductResponse], dependencies=admin_only)
def get_products_admin(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    category_code: Optional[str] = Query(None, min_length=1, max_length=20),
    db: Session = Depends(get_db),
):
    return product_service.list_products(db, skip=skip, limit=limit, category_code=category_code)


@router.get("/admin/{product_id}", response_model=schemas.ProductResponse, dependencies=admin_only)
def get_product_admin(product_id: int, db: Session = Depends(get_db)):
    return product_service.get_product(db, product_id)


@router.get("/{sku}", response_model=schemas.ProductPublicResponse)
def get_product_by_sku(sku: str, db: Session = Depends(get_db)):
    return product_service.get_product_by_sku(db, sku)


@router.patch("/{product_id}", response_model=schemas.ProductResponse, dependencies=admin_only)
def update_product(product_id: int, update: schemas.ProductUpdate, db: Session = Depends(get_db)):
    product = product_service.get_product(db, product_id)
    return product_service.update_product(db, product, update.model_dump(exclude_unset=True))


@router.patch("/by-sku/{sku}", response_model=schemas.ProductResponse, dependencies=admin_only)
def update_product_by_sku(sku: str, update: schemas.ProductSkuUpdate, db: Session = Depends(get_db)):
    product = product_service.get_product_by_sku(db, sku)
    return product_service.update_product(db, product, update.model_dump(exclude_unset=True))


@router.post("/by-sku/{sku}/video", response_model=schemas.ProductVideoResponse, dependencies=admin_only)
def create_product_video(
    sku: str,
    duration: float = Query(7.0, ge=3, le=15, description="مدة الريلز بالثواني"),
    fit: Literal["blur", "crop"] = Query("blur", description="blur: الصورة كاملة فوق خلفية مموّهة، crop: ملء الإطار بالقص"),
    overwrite: bool = Query(False, description="استبدال فيديو موجود (القديم يُنقل إلى MEDIA_BACKUP_DIR)"),
    audio: Optional[str] = Query(None, max_length=120, description="اسم ملف صوتي داخل AUDIO_ROOT لدمجه (اختياري)"),
    db: Session = Depends(get_db),
):
    """يبني ريلز MP4 عمودي (720×1280) من صور المنتج بـ ffmpeg ويحفظ رابطه في المنتج.
    دالة عادية (لا async) فيشغّلها FastAPI في خيط منفصل ولا تحجب باقي الطلبات."""
    result = product_service.generate_product_video(db, sku, duration=duration, fit=fit, overwrite=overwrite, audio=audio)
    return schemas.ProductVideoResponse(
        sku=result.sku, video_url=result.url, size_bytes=result.size_bytes,
        duration=result.duration, images_used=result.images_used, replaced=result.replaced,
        audio=result.audio,
    )


@router.delete("/by-sku/{sku}", dependencies=admin_only)
def delete_product_by_sku(sku: str, db: Session = Depends(get_db)):
    sku = product_service.delete_product(db, product_service.get_product_by_sku(db, sku))
    return {"status": "success", "sku": sku, "message": "تم حذف المنتج بنجاح"}


@router.delete("/clear-all", dependencies=admin_only)
def clear_all_products(confirm: bool = False, db: Session = Depends(get_db)):
    """حذف كل المنتجات من قاعدة البيانات المحلية فقط (لا يحذف من كتالوج ميتا)."""
    if not confirm:
        raise HTTPException(status_code=400, detail="أضف ?confirm=true لتأكيد حذف كل المنتجات")
    deleted = product_service.delete_all_products(db)
    return {"status": "success", "message": f"تم حذف {deleted} منتج بنجاح."}


@router.delete("/{product_id}", dependencies=admin_only)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    sku = product_service.delete_product(db, product_service.get_product(db, product_id))
    return {"status": "success", "id": product_id, "sku": sku, "message": "تم حذف المنتج بنجاح"}


@router.post("/sync-meta/all", status_code=status.HTTP_202_ACCEPTED, dependencies=admin_only)
def sync_all_products_with_meta(background: BackgroundTasks, db: Session = Depends(get_db)):
    product_ids = product_service.all_product_ids(db)
    background.add_task(sync_products_in_background, product_ids)
    return {"status": "queued", "count": len(product_ids), "message": "بدأت مزامنة المنتجات مع Meta في الخلفية"}


@router.post("/{product_id}/sync-meta", dependencies=admin_only)
async def trigger_meta_sync(product_id: int, db: Session = Depends(get_db)):
    product_service.get_product(db, product_id)
    return await sync_product_to_meta(product_id, db)


@router.post("/upload-csv", dependencies=admin_only)
async def upload_products_csv(
    factory_id: int,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """رفع ملف CSV: إضافة المنتجات الجديدة وتحديث ما تغيّر منها، ثم المزامنة مع ميتا في الخلفية."""
    factory = factory_service.get_factory(db, factory_id)
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="يرجى رفع ملف بصيغة CSV فقط")

    content = await file.read()
    try:
        result, touched = product_service.import_csv(db, factory, content)
    except UnicodeDecodeError:
        db.rollback()
        raise HTTPException(status_code=400, detail="ترميز الملف غير صالح، احفظه بصيغة UTF-8")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        db.rollback()
        raise

    db.commit()
    product_ids = [p.id for p in touched]
    if product_ids:
        background.add_task(sync_products_in_background, product_ids)

    return {
        "status": "success",
        "message": "تمت معالجة الملف، والمزامنة مع ميتا تجري في الخلفية.",
        "summary": result["summary"],
        "errors": result["errors"],
    }
