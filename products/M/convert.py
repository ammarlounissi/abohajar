import os
from PIL import Image

CONVERT_EXTENSIONS = {".png", ".bmp", ".webp", ".tiff"}


def process_and_rename_images(root_dir="."):
    total_folders = 0
    total_images = 0

    for current_path, _, files in os.walk(root_dir):
        # 1. أولاً: تحويل جميع الصيغ المختلفة وتوحيد امتدادات .jpeg إلى .jpg
        for filename in files:
            ext = os.path.splitext(filename)[1].lower()
            old_path = os.path.join(current_path, filename)

            # إذا كانت .jpeg مجرد تغيير الامتداد
            if ext in {".jpeg", ".jpe"}:
                new_path = os.path.join(
                    current_path, f"{os.path.splitext(filename)[0]}.jpg"
                )
                os.rename(old_path, new_path)

            # إذا كانت صيغة أخرى (PNG, WEBP..) يتم تحويلها إلى JPG
            elif ext in CONVERT_EXTENSIONS:
                new_path = os.path.join(
                    current_path, f"{os.path.splitext(filename)[0]}.jpg"
                )
                try:
                    with Image.open(old_path) as img:
                        img = img.convert("RGB")
                        img.save(new_path, "JPEG", quality=95)
                    os.remove(old_path)
                except Exception as e:
                    print(f"خطأ في تحويل {filename}: {e}")

        # 2. ثانياً: إعادة التسمية التسلسلية (1.jpg, 2.jpg...) داخل المجلد الحالي
        jpg_files = sorted(
            [
                f
                for f in os.listdir(current_path)
                if os.path.splitext(f)[1].lower() == ".jpg"
            ]
        )

        if not jpg_files:
            continue

        total_folders += 1

        # مرحلة التسمية المؤقتة لتفادي التضارب
        temp_files = []
        for idx, filename in enumerate(jpg_files, start=1):
            old_p = os.path.join(current_path, filename)
            temp_p = os.path.join(current_path, f"__temp_{idx}__.jpg")
            os.rename(old_p, temp_p)
            temp_files.append(temp_p)

        # التسمية النهائية
        for idx, temp_p in enumerate(temp_files, start=1):
            final_p = os.path.join(current_path, f"{idx}.jpg")
            os.rename(temp_p, final_p)
            total_images += 1

        print(
            f"تمت معالجة {len(jpg_files)} صورة وترقيمها في: {current_path}"
        )

    print(
        f"\nاكتملت العملية: تم توحيد وترقيم {total_images} صورة في {total_folders} مجلد."
    )


if __name__ == "__main__":
    process_and_rename_images(".")