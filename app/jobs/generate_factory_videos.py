"""Persistent worker for factory video generation.

    python -m app.jobs.generate_factory_videos
    python -m app.jobs.generate_factory_videos --once
"""
import argparse
import logging
import signal
import time

from app.services import factory_video_jobs

POLL_INTERVAL_SECONDS = 1.0
_stop_requested = False


def _request_stop(_signum, _frame):
    global _stop_requested
    _stop_requested = True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="عامل توليد فيديوهات المصانع")
    parser.add_argument("--once", action="store_true", help="عالج مهمة واحدة ثم انتهِ")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    recovered = factory_video_jobs.recover_interrupted_video_jobs()
    if recovered:
        logging.warning("Marked %d interrupted video job(s) as failed", recovered)

    while not _stop_requested:
        job_id = factory_video_jobs.claim_next_video_job()
        if job_id is None:
            if args.once:
                break
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        factory_video_jobs.process_video_job(job_id)
        if args.once:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
