"""Persistent scheduler for daily factory reel requests.

Run with: python -m app.jobs.factory_reel_scheduler
"""
import logging
import signal
import time

from app.core import job_lock
from app.services.factory_reel_schedules import dispatch_due_schedules

POLL_INTERVAL_SECONDS = 30
LOCK_PATH = "/tmp/hadjret-factory-reel-scheduler.lock"
_stop_requested = False


def _request_stop(_signum, _frame):
    global _stop_requested
    _stop_requested = True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    logging.info("Factory reel scheduler started; polling every %s seconds", POLL_INTERVAL_SECONDS)
    while not _stop_requested:
        try:
            with job_lock.try_acquire(LOCK_PATH) as acquired:
                if acquired:
                    count = dispatch_due_schedules()
                    if count:
                        logging.info("Queued %s scheduled factory reel request(s)", count)
        except Exception:
            logging.exception("Scheduled reel dispatch cycle failed")
        if not _stop_requested:
            time.sleep(POLL_INTERVAL_SECONDS)
    logging.info("Factory reel scheduler stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
