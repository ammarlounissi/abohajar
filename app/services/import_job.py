"""تشغيل الاستيراد التلقائي كمهمة إدارية (فحص المجلد + CSV + مزامنة ميتا).
مشترك بين كل القنوات التي تطلقه (أمر المدير في بوت، لوحة التحكم لاحقاً)."""
import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from app.services.import_report import ImportReport
from app.services.media_import import run_import, sync_touched

logger = logging.getLogger(__name__)

# يمنع تشغيل استيرادين معاً (ضغط مزدوج أو إعادة إرسال)
_lock = asyncio.Lock()

Callback = Optional[Callable[..., Awaitable[None]]]


class ImportBusy(Exception):
    """استيراد آخر جارٍ الآن."""


@dataclass
class ImportJobResult:
    report: ImportReport
    sync_failed: Optional[int] = None   # عدد ما فشلت مزامنته، أو None إن لم تجرِ مزامنة
    sync_error: bool = False            # تعذرت المزامنة كلها (المنتجات محفوظة وحالتها pending)


async def run_import_job(*, on_started: Callback = None, on_written: Callback = None) -> ImportJobResult:
    """ترفع ImportBusy إن كان استيراد جارياً، وImportConfigError عند خلل الإعداد، وأي استثناء آخر كما هو.
    on_started(): بعد أخذ القفل. on_written(count): بعد كتابة القاعدة وقبل المزامنة."""
    if _lock.locked():
        raise ImportBusy()
    async with _lock:
        if on_started:
            await on_started()
        # الفحص يقرأ ملفات كثيرة، فنشغّله في خيط منفصل كي لا يتجمد البوت
        report = await asyncio.to_thread(run_import, apply=True)
        result = ImportJobResult(report)
        if report.touched_ids:
            if on_written:
                await on_written(len(report.touched_ids))
            try:
                result.sync_failed = await sync_touched(report.touched_ids)
            except Exception:
                logger.exception("Meta sync after import failed")
                result.sync_error = True
        return result
