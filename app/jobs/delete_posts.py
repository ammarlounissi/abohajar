"""حذف المنشورات من قاعدة البيانات (الطرفية فقط).

    python -m app.jobs.delete_posts                  # معاينة: يعرض العدد دون حذف
    python -m app.jobs.delete_posts --yes            # حذف جميع المنشورات
    python -m app.jobs.delete_posts --status failed --yes   # حذف المنشورات الفاشلة فقط

يحذف صفوف posts مع post_deliveries وpublish_reports المرتبطة بها.
لا يحذف المنشورات المنشورة فعلاً على فيسبوك/إنستغرام؛ هذا حذف من قاعدة البيانات المحلية فقط.
"""
import argparse
import sys

from sqlalchemy import delete, func, select

from app.core.database import SessionLocal
from app.models import Post, PostDelivery, PostStatus, PublishReport


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="حذف المنشورات")
    parser.add_argument("--status", choices=[s.value for s in PostStatus],
                        help="حذف المنشورات بهذه الحالة فقط (الافتراضي: الكل)")
    parser.add_argument("--yes", action="store_true", help="تنفيذ الحذف فعلاً (بدونه معاينة فقط)")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        cond = [Post.status == PostStatus(args.status)] if args.status else []
        ids = select(Post.id).where(*cond)
        n_posts = db.scalar(select(func.count()).select_from(Post).where(*cond))
        n_deliv = db.scalar(select(func.count()).select_from(PostDelivery).where(PostDelivery.post_id.in_(ids)))
        n_rep = db.scalar(select(func.count()).select_from(PublishReport).where(PublishReport.post_id.in_(ids)))
        print(f"منشورات: {n_posts} | تسليمات: {n_deliv} | تقارير: {n_rep}")

        if not n_posts:
            print("لا شيء للحذف.")
            return 0
        if not args.yes:
            print("معاينة فقط. أضف --yes للتنفيذ.")
            return 0

        # الترتيب مهم: الأبناء أولاً بسبب المفاتيح الأجنبية
        db.execute(delete(PostDelivery).where(PostDelivery.post_id.in_(ids)))
        db.execute(delete(PublishReport).where(PublishReport.post_id.in_(ids)))
        db.execute(delete(Post).where(*cond))
        db.commit()
        print(f"✅ حُذف {n_posts} منشور.")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"❌ {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
