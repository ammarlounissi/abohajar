"""أخطاء طبقة الخدمات. لا تعرف شيئاً عن HTTP ولا عن أي قناة:
كل قناة تترجمها بطريقتها (الـ API إلى رموز HTTP، والبوتات إلى رسائل)."""


class ServiceError(Exception):
    """أساس كل أخطاء الخدمات. رسالتها مناسبة للعرض على المستخدم."""


class NotFoundError(ServiceError):
    """العنصر المطلوب غير موجود."""


class DuplicateError(ServiceError):
    """قيمة يجب أن تكون فريدة (SKU، رقم هاتف...) مستخدمة مسبقاً."""


class ConflictError(ServiceError):
    """العملية تتعارض مع حالة البيانات الحالية (مثل حذف مصنع لديه منتجات)."""


class InvalidInputError(ServiceError):
    """مدخل لا تقبله قواعد العمل."""
