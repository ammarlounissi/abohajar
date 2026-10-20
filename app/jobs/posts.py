"""أوامر مساعدة لإدارة المنشورات من الطرفية:

    python -m app.jobs.posts list [--status approved]
    python -m app.jobs.posts approve <id>      # draft/scheduled/failed -> approved (الفاشل يُعاد بهذا الأمر)
    python -m app.jobs.posts publish <id>      # نشر فوري لمنشور بعينه (يتجاوز الموعد والموافقة والسقف)
    python -m app.jobs.posts show <id>         # حالة كل منصة وآخر التقارير
"""
import argparse
import asyncio
import sys

from app.core.database import SessionLocal
from app.models import PostStatus
from app.services import post_publisher
from app.services.errors import ServiceError


def _channels(post) -> str:
    return ",".join(d.channel.label for d in post.deliveries) or "-"


def _line(post) -> str:
    caption = (post.description or "").replace("\n", " ")[:40]
    return f"{post.id:>4}  {post.status.value:<10} {post.media_type:<7} {_channels(post):<30} {caption}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="إدارة منشورات النشر التلقائي")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list", help="عرض المنشورات")
    p_list.add_argument("--status", choices=[s.value for s in PostStatus])
    p_list.add_argument("--limit", type=int, default=50)
    p_approve = sub.add_parser("approve", help="اعتماد منشور للنشر")
    p_approve.add_argument("post_id", type=int)
    p_publish = sub.add_parser("publish", help="نشر فوري لمنشور بعينه")
    p_publish.add_argument("post_id", type=int)
    p_show = sub.add_parser("show", help="تفاصيل منشور")
    p_show.add_argument("post_id", type=int)
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        if args.cmd == "list":
            status = PostStatus(args.status) if args.status else None
            for post in post_publisher.list_posts(db, status, args.limit):
                print(_line(post))
            return 0
        if args.cmd == "approve":
            try:
                post = post_publisher.approve_post(db, args.post_id)
            except ServiceError as exc:
                print(f"❌ {exc}")
                return 1
            print(f"✅ اعتُمد المنشور {post.id} ({_channels(post)})")
            return 0
        if args.cmd == "publish":
            try:
                post_publisher.claim_post(db, args.post_id)
            except ServiceError as exc:
                print(f"❌ {exc}")
                return 1
            asyncio.run(post_publisher.publish_claimed_in_background(args.post_id))
            db.expire_all()
            return _show(db, args.post_id)
        return _show(db, args.post_id)
    finally:
        db.close()


def _show(db, post_id: int) -> int:
    from app import models
    post = db.get(models.Post, post_id)
    if not post:
        print("❌ المنشور غير موجود")
        return 1
    print(_line(post))
    if post.error:
        print(f"  error: {post.error}")
    for d in post.deliveries:
        print(f"  {d.channel.label} (#{d.channel_id} {d.channel.name}): {d.status} attempts={d.attempts} remote_id={d.remote_id or '-'} {d.error or ''}")
    return 1 if post.status == models.PostStatus.FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
