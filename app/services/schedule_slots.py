"""توزيع المنشورات على مواعيد يومية ثابتة (بلا قاعدة بيانات ولا شبكة، فيسهل اختباره).

times = ساعات النشر اليومية بتوقيت المنطقة الزمنية المضبوطة (الجزائر افتراضياً)، والنتيجة تُخزَّن UTC
في posts.scheduled_at. مثال: times=[8, 14, 18] و6 منشورات = اليوم الأول 8 و14 و18، واليوم الثاني كذلك.
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, List, Optional, Sequence, Set
from zoneinfo import ZoneInfo

MAX_SCAN_DAYS = 3660


def parse_times(values: Any) -> List[time]:
    """يقبل [8, 14, "18:30"] أو النص "8,14,18"؛ يرجع أوقاتاً فريدة مرتبة. يرفع ValueError لقيمة غير صالحة."""
    if isinstance(values, str):
        values = [part for part in values.split(",") if part.strip()]
    if not isinstance(values, (list, tuple)):
        raise ValueError("times يجب أن تكون قائمة ساعات مثل [8, 14, 18]")
    parsed = set()
    for value in values:
        if isinstance(value, bool):
            raise ValueError(f"وقت غير صالح: {value!r}")
        if isinstance(value, int):
            hour, minute = value, 0
        elif isinstance(value, str):
            head, _, tail = value.strip().partition(":")
            if not head.isdigit() or (tail and not tail.isdigit()):
                raise ValueError(f"وقت غير صالح: {value!r}")
            hour, minute = int(head), int(tail or 0)
        else:
            raise ValueError(f"وقت غير صالح: {value!r}")
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"وقت خارج النطاق (0-23 ساعة، 0-59 دقيقة): {value!r}")
        parsed.add(time(hour, minute))
    if not parsed:
        raise ValueError("أدخل وقت نشر واحداً على الأقل")
    return sorted(parsed)


def generate_slots(
    count: int,
    times: Sequence[time],
    *,
    now_utc: datetime,
    tz: ZoneInfo,
    start_date: Optional[date] = None,
    occupied: Iterable[datetime] = (),
) -> List[datetime]:
    """أول count موعداً (UTC بلا tzinfo) بعد now_utc، يتخطى المواعيد المشغولة (occupied بـ UTC أيضاً).
    start_date: أول يوم محلي يُبدأ منه (الافتراضي اليوم محلياً)؛ ما فات من مواعيده اليوم يُتخطى."""
    if count <= 0:
        return []
    taken: Set[datetime] = set(occupied)
    local_now = now_utc.replace(tzinfo=timezone.utc).astimezone(tz)
    day = start_date or local_now.date()
    slots: List[datetime] = []
    for _ in range(MAX_SCAN_DAYS):
        for t in times:
            slot = datetime.combine(day, t, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
            if slot <= now_utc or slot in taken:
                continue
            slots.append(slot)
            if len(slots) == count:
                return slots
        day += timedelta(days=1)
    raise ValueError("تعذّر توزيع المنشورات على المواعيد المطلوبة")
