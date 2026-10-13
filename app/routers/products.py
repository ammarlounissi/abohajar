import re
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.database import get_db
from app.core.security import require_admin
from app.services.meta import sync_product_to_meta, sync_products_in_background
from app.services.products import import_csv

router = APIRouter(prefix="/api/products", tags=["products"])
admin_only = [Depends(require_admin)]


def get_product_or_404(product_id: int, db: Session) -> models.Product:
    product = db.query(models.Product).filter(models.Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="المنتج غير موجود")
    return product


def get_product_by_sku_or_404(sku: str, db: Session) -> models.Product:
    normalized_sku = sku.strip().upper()
    if not re.fullmatch(schemas.SKU_PATTERN, normalized_sku):
        raise HTTPException(status_code=422, detail="رمز SKU غير صالح")
    product = db.query(models.Product).filter(models.Product.sku == normalized_sku).first()
    if not product:
        raise HTTPException(status_code=404, detail="المنتج غير موجود")
    return product


@router.post("/", response_model=schemas.ProductResponse, status_code=status.HTTP_201_CREATED, dependencies=admin_only)
def create_product(product: schemas.ProductCreate, db: Session = Depends(get_db)):
    if not db.query(models.Factory).filter(models.Factory.id == product.factory_id).first():
        raise HTTPException(status_code=404, detail="المصنع غير موجود")
    if db.query(models.Product).filter(models.Product.sku == product.sku).first():
        raise HTTPException(status_code=400, detail="رمز SKU مستخدم مسبقاً")

    new_product = models.Product(**product.model_dump())
    db.add(new_product)
    db.commit()
    db.refresh(new_product)
    return new_product


@router.get("/", response_model=List[schemas.ProductPublicResponse])
def get_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    category_code: Optional[str] = Query(None, min_length=1, max_length=20),
    tag: Optional[str] = Query(None, min_length=1, max_length=50),
    db: Session = Depends(get_db),
):
    query = db.query(models.Product)
    if category_code:
        query = query.filter(models.Product.category_code == category_code.strip().upper())
    if tag:
        query = query.filter(models.Product.tags.contains([tag.strip()]))
    return query.order_by(models.Product.id).offset(skip).limit(limit).all()


@router.get("/admin", response_model=List[schemas.ProductResponse], dependencies=admin_only)
def get_products_admin(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    category_code: Optional[str] = Query(None, min_length=1, max_length=20),
    db: Session = Depends(get_db),
):
    query = db.query(models.Product)
    if category_code:
        query = query.filter(models.Product.category_code == category_code.strip().upper())
    return query.order_by(models.Product.id).offset(skip).limit(limit).all()


@router.get("/admin/{product_id}", response_model=schemas.ProductResponse, dependencies=admin_only)
def get_product_admin(product_id: int, db: Session = Depends(get_db)):
    return get_product_or_404(product_id, db)


@router.get("/{sku}", response_model=schemas.ProductPublicResponse)
def get_product_by_sku(sku: str, db: Session = Depends(get_db)):
    return get_product_by_sku_or_404(sku, db)


@router.patch("/{product_id}", response_model=schemas.ProductResponse, dependencies=admin_only)
def update_product(product_id: int, update: schemas.ProductUpdate, db: Session = Depends(get_db)):
    product = get_product_or_404(product_id, db)
    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(product, key, value)
    product.sync_status = models.SyncStatus.PENDING
    db.commit()
    db.refresh(product)
    return product


@router.patch("/by-sku/{sku}", response_model=schemas.ProductResponse, dependencies=admin_only)
def update_product_by_sku(sku: str, update: schemas.ProductSkuUpdate, db: Session = Depends(get_db)):
    product = get_product_by_sku_or_404(sku, db)
    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(product, key, value)
    product.sync_status = models.SyncStatus.PENDING
    db.commit()
    db.refresh(product)
    return product


@router.delete("/by-sku/{sku}", dependencies=admin_only)
def delete_product_by_sku(sku: str, db: Session = Depends(get_db)):
    product = get_product_by_sku_or_404(sku, db)
    db.delete(product)
    db.commit()
    return {"status": "success", "sku": product.sku, "message": "تم حذف المنتج بنجاح"}


@router.delete("/clear-all", dependencies=admin_only)
def clear_all_products(confirm: bool = False, db: Session = Depends(get_db)):
    """حذف كل المنتجات من قاعدة البيانات المحلية فقط (لا يحذف من كتالوج ميتا)."""
    if not confirm:
        raise HTTPException(status_code=400, detail="أضف ?confirm=true لتأكيد حذف كل المنتجات")
    deleted = db.query(models.Product).delete(synchronize_session=False)
    db.commit()
    return {"status": "success", "message": f"تم حذف {deleted} منتج بنجاح."}


@router.delete("/{product_id}", dependencies=admin_only)
def delete_product(product_id: int, db: Session = Depends(get_db)):
    product = get_product_or_404(product_id, db)
    sku = product.sku
    db.delete(product)
    db.commit()
    return {"status": "success", "id": product_id, "sku": sku, "message": "تم حذف المنتج بنجاح"}


@router.post("/sync-meta/all", status_code=status.HTTP_202_ACCEPTED, dependencies=admin_only)
def sync_all_products_with_meta(background: BackgroundTasks, db: Session = Depends(get_db)):
    product_ids = [row.id for row in db.query(models.Product.id).all()]
    background.add_task(sync_products_in_background, product_ids)
    return {"status": "queued", "count": len(product_ids), "message": "بدأت مزامنة المنتجات مع Meta في الخلفية"}


@router.post("/{product_id}/sync-meta", dependencies=admin_only)
async def trigger_meta_sync(product_id: int, db: Session = Depends(get_db)):
    get_product_or_404(product_id, db)
    return await sync_product_to_meta(product_id, db)


@router.post("/upload-csv", dependencies=admin_only)
async def upload_products_csv(
    factory_id: int,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """رفع ملف CSV: إضافة المنتجات الجديدة وتحديث ما تغيّر منها، ثم المزامنة مع ميتا في الخلفية."""
    factory = db.query(models.Factory).filter(models.Factory.id == factory_id).first()
    if not factory:
        raise HTTPException(status_code=404, detail="المصنع غير موجود")
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="يرجى رفع ملف بصيغة CSV فقط")

    content = await file.read()
    try:
        result, touched = import_csv(db, factory, content)
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
