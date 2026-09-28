import os

def create_folders():
    # 1. طلب البادئة (Prefix) من المستخدم
    prefix = input("أدخل البادئة (Prefix) للمجلدات (مثال: M): ").strip()
    
    # 2. طلب عدد المجلدات مع التحقق من صحة المدخلات
    while True:
        try:
            count = int(input("أدخل عدد المجلدات المراد إنشاؤها: "))
            if count > 0:
                break
            print("الرجاء إدخال رقم أكبر من 0.")
        except ValueError:
            print("خطأ: يرجى إدخال رقم صحيح.")

    # تحديد المسار الحالي الذي تم تشغيل السكريبت منه
    current_directory = os.getcwd()
    print(f"\nسيتم إنشاء المجلدات في المسار الحالي: {current_directory}\n")

    # 3. إنشاء المجلدات بالتنسيق المطلوبة (M001, M002, ...)
    created_count = 0
    for i in range(1, count + 1):
        # zfill(3) تضمن كتابة الأرقام بثلاث خانات مع أصفار على اليسار
        folder_name = f"{prefix}{str(i).zfill(3)}"
        folder_path = os.path.join(current_directory, folder_name)

        try:
            os.makedirs(folder_path, exist_ok=True)
            print(f"✓ تم إنشاء المجلد: {folder_name}")
            created_count += 1
        except Exception as e:
            print(f"✗ فشل إنشاء المجلد {folder_name}: {e}")

    print(f"\nاكتملت العملية! تم إنشاء {created_count} مجلد بنجاح.")

if __name__ == "__main__":
    create_folders()