"""بناء ريلز (MP4 عمودي ~7 ثوانٍ) من صور المنتجات بـ ffmpeg وحفظ رابطه في القاعدة.

    python -m scripts.make_videos B001                  # منتج واحد
    python -m scripts.make_videos B001 B002 PK010       # عدة منتجات
    python -m scripts.make_videos --all                 # كل المنتجات التي لا فيديو لها
    python -m scripts.make_videos --all --overwrite     # إعادة بناء الكل (القديم يُنقل إلى MEDIA_BACKUP_DIR)
    python -m scripts.make_videos B001 --duration 8 --fit crop
    python -m scripts.make_videos B001 --audio music1.mp3   # دمج صوت من AUDIO_ROOT
    python -m scripts.make_videos --list-audio              # الملفات الصوتية المتاحة

الخيار --dry-run يعرض ما سيُبنى دون كتابة شيء. يعيد الرمز 1 إن فشل أي منتج.
"""
import argparse
import sys

from app import models
from app.core.database import SessionLocal
from app.services import media, products as product_service, video_maker
from app.services.errors import ServiceError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="بناء ريلز المنتجات من صورها")
    parser.add_argument("skus", nargs="*", help="رموز المنتجات (SKU)")
    parser.add_argument("--all", action="store_true", help="كل المنتجات (التي لا فيديو لها فقط، إلا مع --overwrite)")
    parser.add_argument("--overwrite", action="store_true", help="استبدال الفيديو الموجود")
    parser.add_argument("--duration", type=float, default=video_maker.DEFAULT_DURATION, help="المدة بالثواني (الافتراضي 7)")
    parser.add_argument("--fit", choices=video_maker.FIT_MODES, default="blur",
                        help="blur: الصورة كاملة فوق خلفية مموّهة (الافتراضي)، crop: ملء الإطار بالقص")
    parser.add_argument("--audio", metavar="FILE", help="اسم ملف صوتي داخل AUDIO_ROOT يُدمج في الفيديو")
    parser.add_argument("--list-audio", action="store_true", help="اعرض الملفات الصوتية المتاحة وانتهِ")
    parser.add_argument("--dry-run", action="store_true", help="اعرض فقط ما سيُبنى")
    args = parser.parse_args(argv)

    if args.list_audio:
        files = video_maker.list_audio_files()
        print("\n".join(files) if files else "لا ملفات صوتية (تحقق من AUDIO_ROOT)")
        return 0
    if bool(args.skus) == args.all:
        parser.error("حدّد رموز منتجات أو --all (واحداً منهما)")

    db = SessionLocal()
    try:
        if args.all:
            skus = [sku for (sku,) in db.query(models.Product.sku).order_by(models.Product.sku)]
        else:
            skus = list(dict.fromkeys(s.strip().upper() for s in args.skus))

        done = skipped = failed = 0
        for sku in skus:
            try:
                if args.all and not args.overwrite and video_maker.has_video(sku):
                    skipped += 1
                    continue
                if args.dry_run:
                    n = len(video_maker.list_source_images(sku)[:video_maker.MAX_IMAGES])
                    print(f"[dry-run] {sku}: {n} صورة -> {video_maker.public_video_url(sku)}")
                    continue
                result = product_service.generate_product_video(
                    db, sku, duration=args.duration, fit=args.fit, overwrite=args.overwrite, audio=args.audio,
                )
                done += 1
                print(f"✅ {sku}: {result.images_used} صورة، {result.size_bytes / 1024:.0f}KB -> {result.url}")
            except (ServiceError, media.InvalidSkuError) as exc:
                failed += 1
                print(f"❌ {sku}: {exc}")
        print(f"\nتم: {done} | متجاوَز (له فيديو): {skipped} | فشل: {failed}")
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
