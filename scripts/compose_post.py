"""بناء منشور من منتج (SKU) بنص خاص بكل منصة، ثم نشره. القوالب في app/services/post_templates.py.

    python -m scripts.compose_post B001                    # يعرض نص كل قناة فقط
    python -m scripts.compose_post B001 --apply            # ينشئ المنشور مسودة ويطبع رقمه
    python -m scripts.compose_post B001 --apply --channel 1 --channel 3   # قنوات بعينها (معرّفها في social_channels)

ثم النشر الفوري:
    python -m app.jobs.posts publish <id>
"""
import argparse
import sys

from app import models
from app.core.database import SessionLocal
from app.models import PostApproval, PostStatus
from app.services import post_publisher
from app.services.errors import ServiceError
from app.services.post_templates import build_caption


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="بناء منشور من منتج")
    parser.add_argument("sku")
    parser.add_argument("--phone", default="", help="رقم واتساب (الافتراضي POST_CONTACT_PHONE من الإعدادات)")
    parser.add_argument("--channel", type=int, action="append", default=[], metavar="ID",
                        help="معرّف القناة الداخلي (social_channels.id)، يتكرر. الافتراضي: كل القنوات المفعّلة")
    parser.add_argument("--apply", action="store_true", help="أنشئ المنشور مسودة (بدونه: عرض فقط)")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        product = db.query(models.Product).filter(models.Product.sku == args.sku.strip().upper()).first()
        if not product:
            print(f"❌ لا منتج بالرمز {args.sku}")
            return 1
        channel_ids = list(dict.fromkeys(args.channel)) or post_publisher.active_channel_ids(db, product.factory_id)
        channels = {c.id: c for c in db.query(models.SocialChannel).filter(models.SocialChannel.id.in_(channel_ids))}
        unknown = [c for c in channel_ids if c not in channels]
        foreign = [cid for cid, channel in channels.items() if channel.factory_id != product.factory_id]
        if unknown or foreign or not channels:
            print(
                "❌ القنوات غير موجودة أو لا تتبع مصنع المنتج أو لا توجد قناة مفعّلة: "
                f"{unknown or foreign or 'سجّل قناة للمصنع في /api/social-channels'}"
            )
            return 1
        captions = {cid: build_caption(product, channels[cid].platform, args.phone) for cid in channel_ids}

        print(f"الصورة: {product.primary_media_url}")
        for cid in channel_ids:
            c = channels[cid]
            print(f"\n=== #{c.id} {c.label} {c.name}{'' if c.is_active else ' (معطّلة)'} ===\n{captions[cid]}")
        if not args.apply:
            print("\n🔎 عرض فقط. أضف --apply لإنشاء المسودة.")
            return 0

        post = models.Post(
            factory_id=product.factory_id, media_type="image", media_urls=[product.primary_media_url],
            description=build_caption(product, "facebook", args.phone),    # الافتراضي لقناة بلا نص خاص
            sku=product.sku, status=PostStatus.DRAFT, approval=PostApproval.PENDING,
        )
        db.add(post)
        db.flush()
        try:
            post_publisher.set_channels(db, post, channel_ids)     # يعمل commit
        except ServiceError as exc:
            db.rollback()
            print(f"❌ {exc}")
            return 1
        for delivery in post.deliveries:
            delivery.caption = captions[delivery.channel_id]
        db.commit()
        print(f"\n✅ أُنشئت المسودة رقم {post.id}\nللنشر الآن: python -m app.jobs.posts publish {post.id}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
