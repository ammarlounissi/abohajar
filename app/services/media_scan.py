"""فحص مجلد الصور وربطه بملف CSV لبناء بيانات المنتجات تلقائياً.

المنطق هنا لا يلمس قاعدة البيانات (القراءة فقط)، والكتابة في scripts/import_from_media.py.

البنية المقروءة:  {MEDIA_ROOT}/{الفئة}/{SKU}/{رقم}.{امتداد}      مثل  B/B001/1.jpg، B/B001/2.jpg
الرابط: {IMAGE_BASE_URL}/{SKU}/{رقم}.{امتداد الملف الفعلي}  مثل  https://hadjretbladi.com/B001/22.jpg
"""
import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

from app.services import media

REQUIRED_COLUMNS = {"sku", "title", "price"}
_CATEGORY_DIR_RE = re.compile(r"[A-Z]+")
_NUMBER_RE = re.compile(r"[0-9]+")        # لا نستعمل isdigit لأنها تقبل الأرقام العربية-الهندية


@dataclass
class ScanResult:
    images: Dict[str, List[int]] = field(default_factory=dict)   # SKU -> أرقام الصور مرتبة
    empty: List[str] = field(default_factory=list)               # مجلدات بلا أي صورة صالحة
    warnings: List[str] = field(default_factory=list)


def scan_media_root(root: Path) -> ScanResult:
    result = ScanResult()
    for cat_dir in sorted(root.iterdir()):
        # نتجاهل الملفات (مثل products.csv) والمجلدات المخفية (.stfolder) وغير المطابقة للفئات
        if not cat_dir.is_dir() or not _CATEGORY_DIR_RE.fullmatch(cat_dir.name):
            continue
        for sku_dir in sorted(cat_dir.iterdir()):
            if not sku_dir.is_dir() or sku_dir.name.startswith("."):
                continue
            sku = sku_dir.name
            if not media.is_valid_media_sku(sku) or media.category_of(sku) != cat_dir.name:
                result.warnings.append(f"مجلد خارج البنية المعتمدة: {cat_dir.name}/{sku}")
                continue
            _scan_product_dir(sku, sku_dir, result)
    return result


def _scan_product_dir(sku: str, folder: Path, result: ScanResult) -> None:
    found: Dict[int, List[str]] = {}               # الرقم -> أسماء الملفات (بالترتيب)
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        if ".sync-conflict-" in f.name:
            result.warnings.append(f"{sku}: ملف تعارض مزامنة تم تجاهله ({f.name})")
            continue
        if not _NUMBER_RE.fullmatch(f.stem):
            continue                                   # ليس اسماً رقمياً (فيديو، غلاف...)
        if f.suffix.lower() not in media.IMAGE_EXTENSIONS:
            continue                                   # مثل mp4
        index = int(f.stem)
        if index == 0:
            result.warnings.append(f"{sku}: الصورة رقم 0 تم تجاهلها ({f.name})")
            continue
        found.setdefault(index, []).append(f.name)

    for index, names in found.items():
        if len(names) > 1:
            result.warnings.append(f"{sku}: الرقم {index} موجود بأكثر من ملف {names}، اعتُمد الأول")

    if not found:
        result.empty.append(sku)
        return
    indexes = sorted(found)
    if indexes != list(range(1, len(indexes) + 1)):
        result.warnings.append(f"{sku}: الترقيم غير متسلسل من 1 {indexes}")
    result.images[sku] = [(i, Path(found[i][0]).suffix) for i in indexes]


def read_csv_rows(path: Path) -> Tuple[Dict[str, dict], List[str]]:
    """يقرأ ملف sku/title/price. تُعيد (SKU -> {title, price}، تحذيرات).
    يتحمل BOM الخاص بـ Excel والفاصلة المنقوطة والفاصلة العشرية."""
    text = path.read_text(encoding="utf-8-sig")
    header_line = text.splitlines()[0] if text.strip() else ""
    if not header_line:
        raise ValueError("ملف CSV فارغ")
    delimiter = max((",", ";", "\t"), key=header_line.count)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    columns = {c.strip().lower() for c in (reader.fieldnames or []) if c}
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise ValueError(f"الملف ينقصه الأعمدة التالية: {', '.join(sorted(missing))}")

    rows: Dict[str, dict] = {}
    warnings: List[str] = []
    for line_no, raw in enumerate(reader, start=2):
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        sku = row.get("sku", "").upper()
        if not sku:
            continue
        if sku in rows:
            warnings.append(f"السطر {line_no}: الرمز {sku} مكرر في الملف، اعتُمد السطر الأخير")
        rows[sku] = {"title": row.get("title", ""), "price": row.get("price", "").replace(",", ".")}
    return rows, warnings


@dataclass
class CatalogBuild:
    records: List[dict] = field(default_factory=list)       # جاهزة للتحقق ثم الكتابة
    images_without_csv: List[str] = field(default_factory=list)
    csv_without_images: List[str] = field(default_factory=list)


def build_records(scan: ScanResult, rows: Dict[str, dict]) -> CatalogBuild:
    """يدمج نتيجة الفحص مع CSV. لا ينشأ منتج إلا إن توفر له صورة واحدة على الأقل
    (حقل الصورة إلزامي في القاعدة وفي كتالوج ميتا) سطر في CSV (العنوان والسعر)."""
    build = CatalogBuild()
    for sku, indexes in sorted(scan.images.items()):
        row = rows.get(sku)
        if row is None:
            build.images_without_csv.append(sku)
            continue
        urls = [media.public_image_url(sku, ext, i) for i, ext in indexes]
        build.records.append({
            "sku": sku,
            "title": row["title"],
            "price": row["price"],
            "primary_media_url": urls[0],
            "additional_media_urls": urls[1:],
        })
    build.csv_without_images = sorted(set(rows) - set(scan.images))
    return build
