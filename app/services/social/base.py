"""العقد المشترك لمحوّلات النشر الاجتماعي. المحوّل الجديد (تيليجرام لاحقاً) = ملف واحد في هذا المجلد.

لا نصوص عربية هنا: الأخطاء رموز تقنية قصيرة (مثل instagram_image_must_be_jpeg) أو نص Meta كما جاء،
وتُحفظ في post_deliveries.error وpublish_reports.message.
"""
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from app.models import ReportKind

# حدود المنصات المعروفة
INSTAGRAM_CAPTION_LIMIT = 2200

# أكواد أخطاء Graph API
_TOKEN_CODES = {190, 463, 467}              # 190 رمز غير صالح، 463 منتهٍ، 467 غير صالح (سبب عطل أغسطس)
_LIMIT_CODES = {4, 17, 32, 613}             # حدود معدل الاستدعاء
_LIMIT_SUBCODES = {2207042}                 # إنستغرام: بلغ حد النشر اليومي
_MEDIA_CODES = {100, 324, 351, 352, 9004}   # معاملات/وسائط غير صالحة أو تعذر تنزيلها
_TEMPORARY_CODES = {1, 2}                   # أعطال مؤقتة عند ميتا


class PublishError(Exception):
    """فشل نشر على منصة. kind من ReportKind، وmessage نص مختصر مناسب للتقرير."""

    def __init__(self, kind: ReportKind, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class PublishResult:
    platform: str
    ok: bool
    remote_id: Optional[str] = None
    error: Optional[str] = None
    kind: Optional[ReportKind] = None      # يُملأ عند الفشل فقط


def classify_graph_error(http_status: int, error: Optional[dict]) -> ReportKind:
    """تصنيف خطأ Graph API. الرمز (190/463) أولاً لأنه الأهم في التقرير."""
    error = error or {}
    code, subcode = error.get("code"), error.get("error_subcode")
    if code in _TOKEN_CODES:
        return ReportKind.TOKEN
    if code in _LIMIT_CODES or subcode in _LIMIT_SUBCODES or (code == 9 and subcode in _LIMIT_SUBCODES):
        return ReportKind.LIMIT
    if code in _MEDIA_CODES or (isinstance(subcode, int) and 2207000 <= subcode < 2208000):
        return ReportKind.MEDIA
    if code in _TEMPORARY_CODES or (not error and http_status >= 500):
        return ReportKind.NETWORK
    return ReportKind.UNKNOWN


def url_extension(url: str) -> str:
    """امتداد المسار بحروف صغيرة، دون الاستعلام (?...)."""
    path = urlparse(url).path.lower()
    return path[path.rfind("."):] if "." in path.rsplit("/", 1)[-1] else ""


def is_http_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


class Publisher:
    """أساس المحوّلات. المحوّل يرث ويضبط platform ثم يكتب validate و_publish فقط.

    publish(post): يتحقق أولاً بلا أي طلب شبكة (لا نذهب إلى ميتا بمنشور نعرف أنه سيُرفض)،
    ثم ينفّذ، ويُرجع PublishResult دائماً ولا يرفع PublishError.
    post: أي كائن له description وmedia_type وmedia_urls (نموذج Post أو بديله في الاختبار).
    """

    platform: str = ""

    def validate(self, post) -> Optional[PublishError]:
        raise NotImplementedError

    async def _publish(self, post) -> str:
        """يرجع معرّف المنشور عند المنصة، أو يرفع PublishError."""
        raise NotImplementedError

    async def publish(self, post) -> PublishResult:
        problem = self.validate(post)
        if problem is None:
            try:
                remote_id = await self._publish(post)
            except PublishError as exc:
                problem = exc
            else:
                return PublishResult(platform=self.platform, ok=True, remote_id=str(remote_id))
        return PublishResult(platform=self.platform, ok=False, error=problem.message, kind=problem.kind)
