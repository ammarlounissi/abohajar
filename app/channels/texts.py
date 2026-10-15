"""نصوص المحادثة مع المصانع (عربية)، مشتركة بين كل القنوات.
الخدمات تُرجع رموزاً وبيانات، وهنا فقط تتحول إلى كلام. تغيير الصياغة لا يمس المنطق."""
from app import models
from app.services.import_job import ImportJobResult
from app.services.import_report import format_report
from app.services.product_intake import RejectionCode, StoreStatus

SKU_HELP = "رمز المنتج حروف لاتينية كبيرة متبوعة بأرقام فقط، مثل B001 أو PTK012."

GREETING_KEYWORDS = [
    "السلام عليكم", "سلام عليكم", "السلام", "سلام", "مرحبا",
    "صباح الخير", "مساء الخير", "salam", "slm",
]
YES_WORDS = {"نعم", "اي", "أي", "yes", "oui", "ok"}
NO_WORDS = {"لا", "no", "non"}

CAPTION_FORMAT_ERROR = (
    "⚠️ تنسيق الشرح غير صحيح. اكتب مع الصورة:\n"
    "*اسم المنتج - السعر - رمز SKU*\n"
    "مثال: حجر قالمة بيج - 3200 - B001"
)
IMPORT_BUSY = "⏳ عملية استيراد جارية الآن، انتظر انتهاءها."
IMPORT_STARTED = "⏳ بدأ الاستيراد من مجلد الصور..."
IMPORT_FAILED = "❌ فشل الاستيراد ولم تُكتب أي بيانات. راجع سجل الخادم."
PENDING_EXPIRED = "⚠️ هذا الطلب انتهت صلاحيته أو سبقت معالجته. أعد إرسال الصورة مع الشرح إن أردت."
GENERIC_WELCOME = "مرحباً بك! أرسل كلمة *إضافة منتج* للبدء في رفع منتجاتك."

# حد رسالة واتساب النصية 4096 حرفاً؛ نبقى تحته في كل القنوات
MAX_REPLY_CHARS = 3800


def is_greeting(text: str) -> bool:
    clean = text.lower().strip()
    return any(keyword in clean for keyword in GREETING_KEYWORDS)


def yes_no(text: str):
    """True/False لجواب نعم/لا نصاً، وإلا None."""
    word = text.strip().strip(".!؟?").lower()
    if word in YES_WORDS:
        return True
    if word in NO_WORDS:
        return False
    return None


def is_add_product_request(text: str) -> bool:
    return "إضافة منتج" in text or "اضافة منتج" in text


def greeting(factory: models.Factory) -> str:
    return (
        "وعليكم السلام ورحمة الله وبركاته 🌸\n"
        f"أهلاً بك مصنع ({factory.name}) في منصة حجرة بلادي 🏛️.\n\n"
        "أرسل كلمة *إضافة منتج* للبدء في رفع منتجاتك."
    )


def add_product_help(factory: models.Factory) -> str:
    return (
        f"أهلاً بك مصنع ({factory.name}) 🏛️\n\n"
        "لإضافة منتج جديد، أرسل *صورة الحجر* واكتب في الشرح (Caption) التنسيق التالي:\n\n"
        "*اسم المنتج - السعر - رمز SKU*\n\n"
        "📌 مثال:\n"
        "حجر قالمة بيج - 3200 - B001\n\n"
        "(ضع مسافة قبل الشرطة وبعدها)\n"
        f"{SKU_HELP}"
    )


def rejection(code: RejectionCode, sku=None) -> str:
    if code is RejectionCode.BAD_CAPTION:
        return CAPTION_FORMAT_ERROR
    if code is RejectionCode.INVALID_DATA:
        return "⚠️ بيانات غير صالحة. تأكد أن السعر رقم أكبر من الصفر، وأن الرمز صحيح.\n" + SKU_HELP
    if code is RejectionCode.INVALID_SKU:
        return f"⚠️ رمز المنتج ({sku}) غير صالح.\n" + SKU_HELP
    if code is RejectionCode.NO_MEDIA:
        return "⚠️ تعذر قراءة الصورة، يرجى إعادة إرسالها."
    return f"⚠️ رمز المنتج ({sku}) مستخدم مسبقاً، يرجى اختيار رمز آخر."


def update_question(sku: str, title: str, price: float) -> str:
    return (
        f"⚠️ رمز المنتج ({sku}) موجود مسبقاً.\n\n"
        "هل تريد تحديثه بالصورة الجديدة؟\n"
        "• تُستبدل الصورة الرئيسية (تُحفظ القديمة نسخةً احتياطية)\n"
        f"• يُحدَّث الاسم والسعر: {title} - {price:g} دج\n\n"
        "اضغط أحد الزرين، أو اكتب نعم / لا."
    )


def update_cancelled(sku: str) -> str:
    return f"👍 تم إلغاء الطلب. لم يتغيّر شيء في المنتج ({sku})."


def store_failure(status: StoreStatus, sku: str) -> str:
    if status is StoreStatus.DOWNLOAD_FAILED:
        return "⚠️ تعذر تحميل الصورة، يرجى إعادة إرسالها."
    if status is StoreStatus.NOT_IMAGE:
        return "⚠️ الملف ليس صورة مدعومة. أرسل صورة بصيغة JPEG أو PNG أو WebP."
    if status is StoreStatus.SKU_TAKEN:
        return f"⚠️ رمز المنتج ({sku}) مستخدم مسبقاً، يرجى اختيار رمز آخر."
    return "⚠️ تعذر حفظ الصورة. تأكد أنها بصيغة JPEG أو PNG أو WebP وأعد إرسالها."


def stored(product: models.Product, created: bool, synced: bool) -> str:
    if not synced:
        return f"✅ تم حفظ المنتج ({product.sku}) لكن تعذرت مزامنته مع ميتا الآن."
    verb = "إضافة" if created else "تحديث"
    return (
        f"✅ تم {verb} ومزامنة المنتج بنجاح!\n\n"
        f"📦 المنتج: {product.title}\n"
        f"💰 السعر: {product.price:g} دج\n"
        f"🏷️ الرمز: {product.sku}\n"
        f"🆔 معرّف ميتا: {product.meta_product_id}"
    )


def import_written(count: int) -> str:
    return f"✅ كُتب {count} منتجاً. 🔄 جارٍ المزامنة مع ميتا..."


def import_summary(result: ImportJobResult) -> str:
    text = format_report(result.report, limit=5)
    touched = len(result.report.touched_ids)
    if result.sync_failed is not None:
        text += f"\n\n🔄 مزامنة ميتا: نجحت {touched - result.sync_failed}، فشلت {result.sync_failed}"
    elif result.sync_error:
        text += "\n\n⚠️ تعذرت مزامنة ميتا، المنتجات محفوظة وحالتها pending."
    return text[:MAX_REPLY_CHARS]
