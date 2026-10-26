"""خدمة دورية: تنسخ صور مجلدات Google Drive المشارَكة إلى مكتبات المصانع.

    python -m app.jobs.sync_drive_media            # حلقة دائمة كل DRIVE_SYNC_INTERVAL_SECONDS
    python -m app.jobs.sync_drive_media --once     # تشغيل واحد ثم انتهِ
"""
import argparse
import logging
import signal
import threading

from app.core.config import settings
from app.services import drive_sync

_stop = threading.Event()


def _request_stop(_signum, _frame):
    _stop.set()


def run_once() -> None:
    try:
        reports = drive_sync.sync_all()
    except Exception:
        logging.exception("Drive sync run failed")      # rclone/الشبكة: تُعاد المحاولة في الدورة التالية
        return
    for report in reports.values():
        if report.copied or report.skipped or report.failed or report.errors:
            logging.info(
                "Drive sync factory=%s folders=%s copied=%s skipped=%s failed=%s remaining=%s errors=%s",
                report.factory_id, report.folders, report.copied, report.skipped, report.failed,
                report.remaining, report.errors,
            )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="مزامنة صور Google Drive إلى مكتبات المصانع")
    parser.add_argument("--once", action="store_true", help="شغّل دورة واحدة ثم انتهِ")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    while not _stop.is_set():
        run_once()
        if args.once:
            break
        _stop.wait(settings.DRIVE_SYNC_INTERVAL_SECONDS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
