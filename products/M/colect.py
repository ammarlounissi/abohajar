import os
import shutil

# المجلد الرئيسي الذي يحتوي على المجلاات الفرعية ('.' يعني المجلد الحالي)
base_directory = "."

# المجلد الجديد الذي ستجمع فيه كافة الصور
output_directory = os.path.join(base_directory, "All_Images")

# إنشاء مجلد التجميع إذا لم يكن موجوداً
if not os.path.exists(output_directory):
    os.makedirs(output_directory)

# التكرار على جميع عناصر المجلد الرئيسي
for folder_name in os.listdir(base_directory):
    folder_path = os.path.join(base_directory, folder_name)

    # التأكد من أن العنصر هو مجلد فرعي وليس المجلد المستهدف نفسه
    if os.path.isdir(folder_path) and folder_name != "All_Images":
        # المسار المتوقع للصورة داخل المجلد الفرعي
        image_path = os.path.join(folder_path, "1.jpg")

        # التحقق من وجود الصورة 1.jpg
        if os.path.exists(image_path):
            # تحديد الامتداد واسم الصورة الجديد بناءً على اسم المجلد
            new_image_name = f"{folder_name}.jpg"
            destination_path = os.path.join(output_directory, new_image_name)

            # نسخ الصورة وتسميتها بالاسم الجديد في مجلد التجميع
            shutil.copy(image_path, destination_path)
            print(f"تم نسخ: {folder_name}/1.jpg -> All_Images/{new_image_name}")

print("\nاكتملت العملية بنجاح!")