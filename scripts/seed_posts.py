"""ملء جدول posts بمنشورات تجريبية لاختبار النشر التلقائي (خطة الاختبار في وثيقة المرحلة الأولى).

    python -m scripts.seed_posts                              # يعرض ما سيُضاف فقط
    python -m scripts.seed_posts --apply                      # يضيفها كمسودات (draft)
    python -m scripts.seed_posts --apply --approve            # ويعتمدها (approved) مرتّبة بالتسلسل
    python -m scripts.seed_posts --apply --with-failures      # ويضيف حالات الفشل المقصودة
    python -m scripts.seed_posts --clear --apply              # يحذف منشورات التجربة من القاعدة فقط
    python -m scripts.seed_posts --channel facebook:123456 --channel instagram:555   # يوجّهها إلى قناتين بعينهما

بدون --channel تُوجَّه كل منشور إلى كل القنوات المفعّلة في social_channels لمنصته. --channel PLATFORM:ID
(المعرّف الأصلي كما في social_channels) يحصرها في تلك القناة لتلك المنصة. تُحفظ الوجهات بمعرّف القناة الداخلي.

كل نص يبدأ بـ [TEST-n] لتمييزها وحذفها لاحقاً. تحذير: عند اعتمادها وتشغيل الناشر تُنشر فعلاً على الصفحة الحقيقية.
حذفها من القاعدة لا يحذف ما نُشر: فيسبوك بـ DELETE وإنستغرام يدوياً من التطبيق.

الوسائط: صورة JPEG من أول منتج في القاعدة (أو --image-url)، والفيديو يلزمه --video-url
(رابط MP4 عمودي عام)، وبدونه تُتخطى منشورات الفيديو.
"""
import argparse
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select

from app import models
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import PostApproval, PostStatus
from app.services import post_publisher

TAG = "[TEST-"


def build_seed_posts(
    image_url: str, video_url: Optional[str], with_failures: bool, missing_url: str,
) -> List[dict]:
    """قائمة منشورات (dict) بالترتيب. دالة نقية بلا قاعدة بيانات، فتُختبر وحدها.
    "platforms" فيها وصف المنصات المستهدفة فقط (facebook,instagram)، وتحويلها إلى قنوات يتم في main."""
    spec = [
        ("نص فقط على فيسبوك", "text", [], "facebook"),
        ("صورة JPEG على فيسبوك فقط", "image", [image_url], "facebook"),
        ("صورة JPEG على إنستغرام فقط", "image", [image_url], "instagram"),
        ("صورة JPEG على المنصتين", "image", [image_url], "facebook,instagram"),
        ("نص ثانٍ على فيسبوك", "text", [], "facebook"),
        ("صورة ثانية على المنصتين", "image", [image_url], "facebook,instagram"),
    ]
    if video_url:
        spec += [
            ("فيديو على فيسبوك فقط", "reel", [video_url], "facebook"),
            ("ريلز على إنستغرام فقط", "reel", [video_url], "instagram"),
            ("فيديو على المنصتين", "reel", [video_url], "facebook,instagram"),
        ]
    if with_failures:
        webp = image_url.rsplit(".", 1)[0] + ".webp"
        spec += [
            ("فشل مقصود: WebP على إنستغرام", "image", [webp], "instagram"),
            ("فشل مقصود: رابط صورة غير موجود", "image", [missing_url], "facebook,instagram"),
            ("فشل مقصود: نص أطول من 2200 حرف", "image", [image_url], "instagram"),
        ]
    posts = []
    for n, (label, media_type, urls, platforms) in enumerate(spec, start=1):
        caption = f"{TAG}{n}] {label}"
        if "2200" in label:
            caption += " " + "ا" * 2300
        posts.append({"n": n, "media_type": media_type, "media_urls": urls, "platforms": platforms, "description": caption})
    return posts


def _default_image(db) -> Optional[str]:
    for url in db.scalars(select(models.Product.primary_media_url)):
        if (url or "").lower().split("?")[0].endswith((".jpg", ".jpeg")):
            return url
    return None


def _default_factory(db) -> Optional[int]:
    if settings.DEFAULT_FACTORY_ID:
        return settings.DEFAULT_FACTORY_ID
    return db.scalar(select(models.Factory.id).order_by(models.Factory.id).limit(1))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="منشورات تجريبية للنشر التلقائي")
    parser.add_argument("--apply", action="store_true", help="نفّذ فعلاً (بدونه: عرض فقط)")
    parser.add_argument("--approve", action="store_true", help="اعتمدها (approved) ليلتقطها الناشر")
    parser.add_argument("--with-failures", action="store_true", help="أضف حالات الفشل المقصودة")
    parser.add_argument("--clear", action="store_true", help="احذف منشورات التجربة ([TEST-n]) من القاعدة")
    parser.add_argument("--factory-id", type=int)
    parser.add_argument("--image-url", help="رابط صورة JPEG عامة (الافتراضي: من أول منتج JPEG)")
    parser.add_argument("--channel", action="append", default=[], metavar="PLATFORM:ID",
                        help="وجّه منصة إلى قناة بعينها، مثل facebook:123456 (يتكرر لأكثر من منصة)")
    parser.add_argument("--video-url", help="رابط MP4 عمودي عام لمنشورات الفيديو")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        if args.clear:
            old = db.scalars(select(models.Post).where(models.Post.description.like(f"{TAG}%"))).all()
            print(f"منشورات التجربة الموجودة: {len(old)}")
            if args.apply:
                for post in old:
                    db.delete(post)
                db.commit()
                print("🗑️ حُذفت من القاعدة (ما نُشر فعلاً يُحذف من المنصة يدوياً).")
            else:
                print("🔎 عرض فقط، أضف --apply للحذف.")
            return 0

        overrides: Dict[str, int] = {}      # {منصة: معرّف القناة الداخلي} من --channel
        for item in args.channel:
            platform, _, remote_id = item.partition(":")
            platform = platform.lower()
            if not platform or not remote_id:
                print(f"❌ --channel يأخذ PLATFORM:ID مثل facebook:123456، وجدنا: {item!r}")
                return 2
            known = db.scalar(select(models.SocialChannel.id).where(
                models.SocialChannel.platform == platform,
                models.SocialChannel.remote_id == remote_id, models.SocialChannel.is_active.is_(True)))
            if not known:
                print(f"❌ القناة {item} غير مسجلة أو غير مفعّلة في social_channels")
                return 2
            overrides[platform] = known

        def channel_ids_for(platforms: str) -> List[int]:
            ids: List[int] = []
            for platform in platforms.split(","):
                ids += [overrides[platform]] if platform in overrides else post_publisher.active_channel_ids(db, platform)
            return list(dict.fromkeys(ids))

        factory_id = args.factory_id or _default_factory(db)
        if not factory_id or not db.get(models.Factory, factory_id):
            print("❌ لا مصنع صالح: مرّر --factory-id أو اضبط DEFAULT_FACTORY_ID")
            return 2
        image_url = args.image_url or _default_image(db)
        if not image_url:
            print("❌ لا صورة JPEG: مرّر --image-url (رابط عام يفتح من الإنترنت)")
            return 2
        if not args.video_url:
            print("ℹ️ بلا --video-url: تُتخطى منشورات الفيديو.")

        missing = f"{settings.IMAGE_BASE_URL or 'https://hadjretbladi.com'}/ZZ999/1.jpg"
        seeds = build_seed_posts(image_url, args.video_url, args.with_failures, missing)
        now = datetime.utcnow()
        for seed in seeds:
            seed["channel_ids"] = channel_ids_for(seed["platforms"])
            print(f"  {seed['n']:>2}. {seed['media_type']:<5} {seed['platforms']:<20} قنوات={seed['channel_ids']} {seed['description'][:40]}")
        empty = [seed["n"] for seed in seeds if not seed["channel_ids"]]
        if empty:
            print(f"❌ لا قناة مفعّلة لمنشورات: {empty}. سجّل القنوات أولاً في /api/social-channels")
            return 2
        if not args.apply:
            print(f"\n🔎 عرض فقط ({len(seeds)} منشوراً). أضف --apply للإضافة.")
            return 0

        for i, seed in enumerate(seeds):
            approved = args.approve
            post = models.Post(
                factory_id=factory_id, media_type=seed["media_type"], media_urls=seed["media_urls"],
                description=seed["description"],
                status=PostStatus.APPROVED if approved else PostStatus.DRAFT,
                approval=PostApproval.APPROVED if approved else PostApproval.PENDING,
                approved_at=(now + timedelta(seconds=i)) if approved else None,   # يحفظ الترتيب
            )
            db.add(post)
            db.flush()
            post_publisher.set_channels(db, post, seed["channel_ids"])      # يعمل commit
        state = "approved" if args.approve else "draft"
        print(f"\n✅ أُضيف {len(seeds)} منشوراً بحالة {state} للمصنع {factory_id}.")
        if args.approve:
            print("⚠️ المؤقت أو publish_due سيُنشرها فعلاً على الصفحة الحقيقية.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
