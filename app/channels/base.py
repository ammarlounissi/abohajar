"""العقد المشترك بين القنوات.

كل قناة (واتساب، تيليجرام...) مسؤولة عن شيئين فقط:
  1. ترجمة رسالتها الواردة إلى IncomingEvent (المحوّل: adapter).
  2. إرسال الرد وتنزيل الوسائط (العميل: ChannelClient).
وكل ما عدا ذلك (فهم الأوامر، المنطق، الصياغة) يعيش في factory_flow وservices ولا يتكرر.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Sequence, Tuple


class EventKind(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    CHOICE = "choice"     # ضغطة زر (رد سريع / inline button)


@dataclass(frozen=True)
class IncomingEvent:
    channel: str                       # قيمة models.Channel
    sender_id: str                     # معرّف المرسل كما تعرفه القناة (يُطبَّع عند البحث)
    kind: EventKind
    text: str = ""                     # TEXT: نص الرسالة
    media_ref: Optional[str] = None    # IMAGE: مرجع الملف عند القناة
    caption: str = ""                  # IMAGE: الشرح المرفق
    choice_id: str = ""                # CHOICE: معرّف الزر المضغوط


class ChannelClient(Protocol):
    """ما يحتاجه سير المحادثة من القناة. `to` هو sender_id للحدث الوارد."""

    async def send_text(self, to: str, text: str) -> None: ...

    async def send_choices(self, to: str, text: str, choices: Sequence[Tuple[str, str]]) -> None:
        """choices = [(id, عنوان الزر), ...]؛ يصل الضغط حدثاً من نوع CHOICE بالمعرّف نفسه."""

    async def download_media(self, media_ref: str) -> Optional[bytes]:
        """بايتات الملف، أو None عند الفشل."""
