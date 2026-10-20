"""نقطة دخول النشر التلقائي (يشغّلها systemd timer كل يوم 08:00 بتوقيت الجزائر):

    python -m app.jobs.publish_due              # نشر فعلي (منشور واحد جاهز وحان موعده)
    python -m app.jobs.publish_due --dry-run    # يطبع ما كان سينشره ونتيجة التحقق المسبق، بلا نشر ولا حجز

رمز الخروج: 0 سليم، 1 فشل نشر على منصة أو أكثر (يظهر في systemctl --failed وjournalctl)، 2 خلل إعداد.
"""
import argparse
import asyncio
import logging
import sys

from app.core.config import settings
from app.core.database import SessionLocal
from app.services import post_publisher
from app.services.social import build_publishers


def format_summary(summary: post_publisher.RunSummary) -> str:
    lines = []
    if summary.dry_run:
        lines.append("DRY-RUN: لا حجز ولا نشر")
    for post_id in summary.stale_post_ids:
        lines.append(f"STALE post={post_id}: عالق في publishing، لا يُعاد نشره تلقائياً (راجعه يدوياً)")
    if summary.daily_limit_reached:
        lines.append(f"LIMIT: بلغ السقف اليومي ({settings.PUBLISH_DAILY_LIMIT})")
    if not summary.outcomes and not summary.daily_limit_reached:
        lines.append("لا منشور جاهز الآن")
    for outcome in summary.outcomes:
        lines.append(f"post={outcome.post_id} status={outcome.status} channels={','.join(outcome.channels) or '-'}")
        for label in outcome.skipped:
            lines.append(f"  {label}: سبق نشره، تخطّي")
        for result in outcome.results:
            name = result.channel_label or result.platform
            if result.ok:
                lines.append(f"  {name}: OK remote_id={result.remote_id}" if not summary.dry_run
                             else f"  {name}: سيُنشر (اجتاز التحقق المسبق)")
            else:
                kind = result.kind.value if result.kind else "unknown"
                lines.append(f"  {name}: FAILED kind={kind} {result.error}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="نشر المنشور الجاهز على قنواته")
    parser.add_argument("--dry-run", action="store_true", help="اعرض ما سيُنشر دون حجز أو نشر")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not settings.META_ACCESS_TOKEN:
        print("❌ META_ACCESS_TOKEN غير مضبوط في .env")
        return 2

    db = SessionLocal()
    try:
        summary = asyncio.run(post_publisher.publish_due(
            db, publishers=build_publishers(), dry_run=args.dry_run,
        ))
    finally:
        db.close()

    print(format_summary(summary))
    return 1 if summary.failures else 0


if __name__ == "__main__":
    sys.exit(main())
