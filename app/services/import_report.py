"""تقرير الاستيراد التلقائي وتنسيقه نصاً (بلا قاعدة بيانات، لذلك يسهل اختباره).
يستعمله سكريبت الطرفية وأمر واتساب."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


class ImportConfigError(Exception):
    """خلل في الإعداد أو الملفات يمنع الاستيراد (رسالته مناسبة للعرض على المستخدم)."""


@dataclass
class ImportReport:
    csv_path: Path
    csv_rows: int = 0
    folders_with_images: int = 0
    total_images: int = 0
    empty_folders: int = 0
    created: List[str] = field(default_factory=list)
    updated: List[str] = field(default_factory=list)
    unchanged: List[str] = field(default_factory=list)
    images_without_csv: List[str] = field(default_factory=list)
    csv_without_images: List[str] = field(default_factory=list)
    invalid: List[str] = field(default_factory=list)
    foreign: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    applied: bool = False
    backup_path: Optional[Path] = None
    touched_ids: List[int] = field(default_factory=list)


def _section(title: str, items: List[str], limit: Optional[int]) -> List[str]:
    if not items:
        return []
    shown = items if limit is None else items[:limit]
    lines = ["", f"{title} ({len(items)}):"] + [f"   - {i}" for i in shown]
    if len(shown) < len(items):
        lines.append(f"   ... و{len(items) - len(shown)} أخرى")
    return lines


def format_report(report: ImportReport, limit: Optional[int] = 15) -> str:
    """limit = أقصى عدد عناصر تُعرض من كل قائمة (None = الكل)."""
    lines = [
        f"📄 CSV: {report.csv_path} ({report.csv_rows} منتجاً)",
        f"🖼️ مجلدات فيها صور: {report.folders_with_images} (إجمالي {report.total_images} صورة)، "
        f"مجلدات فارغة: {report.empty_folders}",
        "",
        f"➕ جديد: {len(report.created)}   ✏️ تحديث: {len(report.updated)}   ⏸️ بلا تغيير: {len(report.unchanged)}",
    ]
    lines += _section("⚠️ صور بلا سطر في CSV (لن تُضاف)", report.images_without_csv, limit)
    lines += _section("⚠️ في CSV بلا صور (لن تُضاف)", report.csv_without_images, limit)
    lines += _section("❌ بيانات غير صالحة", report.invalid, limit)
    lines += _section("❌ تعارض ملكية", report.foreign, limit)
    lines += _section("ℹ️ تنبيهات", report.warnings, limit)
    if report.applied:
        lines.append("")
        lines.append(f"✅ تمت الكتابة في القاعدة: {len(report.created)} جديد، {len(report.updated)} محدّث.")
        if report.backup_path:
            lines.append(f"💾 نسخة احتياطية: {report.backup_path}")
    return "\n".join(lines)
