"""تجربة بناء منشور من منتج (SKU) ثم نشره. نسخة تجريبية نعدّلها حتى يستقر الشكل،
ثم تُنقل إلى قالب دائم (المرحلة الثانية).

    python -m scripts.compose_post B001                    # يعرض النص فقط
    python -m scripts.compose_post B001 --apply            # ينشئ المنشور مسودة ويطبع رقمه
    python -m scripts.compose_post B001 --apply --channel 1 --channel 3   # قنوات بعينها (معرّفها في social_channels)

ثم النشر الفوري:
    python -m app.jobs.posts publish <id>
"""
import argparse
import re
import sys
from urllib.parse import quote

from app import models
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import PostApproval, PostStatus
from app.services import post_publisher
from app.services.errors import ServiceError

DEFAULT_PHONE = "0549194378"          # يُحوَّل إلى الصيغة الدولية 213549194378
BRAND_TAGS = ["#حجرة_بلادي", "#ديكور_جزائري", "#الجزائر"]


def intl_phone(local: str, country: str = "213") -> str:
    digits = re.sub(r"\D", "", local)
    return digits if digits.startswith(country) else country + digits.lstrip("0")


def hashtags(product) -> str:
    tags = list(BRAND_TAGS)
    tags += [("#" + str(t).strip().replace(" ", "_")) for t in (product.tags or []) if str(t).strip()][:3]
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return " ".join(out)


def build_caption(product, phone: str) -> str:
    name = (product.commercial_name or product.title).strip()
    if not name.startswith("حجر"):
        name = f"حجر {name}"
    lines = [f"📌 {name}"]
    if product.description and product.description.strip():
        lines.append(product.description.strip())
    wa = f"https://wa.me/{intl_phone(phone)}?text=" + quote(f"أريد الاستفسار عن المنتج {product.sku}")
    lines += [
        f"📲 للطلب أو الاستفسار عبر واتساب: {phone}",
        wa,
        f"🔗 تفاصيل المنتج: {settings.product_link(product.sku)}",
        hashtags(product),
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="بناء منشور من منتج")
    parser.add_argument("sku")
    parser.add_argument("--phone", default=DEFAULT_PHONE)
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
        caption = build_caption(product, args.phone)
        channel_ids = list(dict.fromkeys(args.channel)) or post_publisher.active_channel_ids(db)
        channels = {c.id: c for c in db.query(models.SocialChannel).filter(models.SocialChannel.id.in_(channel_ids))}
        unknown = [c for c in channel_ids if c not in channels]
        if unknown or not channels:
            print(f"❌ قنوات غير موجودة أو لا قناة مفعّلة: {unknown or 'سجّل قناة في /api/social-channels'}")
            return 1
        listing = "\n".join(f"  #{c.id} {c.label} {c.name}{'' if c.is_active else ' (معطّلة)'}" for c in channels.values())
        print(f"الصورة: {product.primary_media_url}\nالقنوات:\n{listing}\n{'-' * 40}\n{caption}\n{'-' * 40}")
        if not args.apply:
            print("🔎 عرض فقط. أضف --apply لإنشاء المسودة.")
            return 0
        post = models.Post(
            factory_id=product.factory_id, media_type="image", media_urls=[product.primary_media_url],
            description=caption, sku=product.sku,
            status=PostStatus.DRAFT, approval=PostApproval.PENDING,
        )
        db.add(post)
        db.flush()
        try:
            post_publisher.set_channels(db, post, channel_ids)     # يعمل commit
        except ServiceError as exc:
            db.rollback()
            print(f"❌ {exc}")
            return 1
        print(f"✅ أُنشئت المسودة رقم {post.id}\nللنشر الآن: python -m app.jobs.posts publish {post.id}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
