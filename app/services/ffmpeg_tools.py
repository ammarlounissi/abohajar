"""العثور على ffmpeg وffprobe.

خدمات systemd وبيئات التشغيل المقيَّدة كثيراً ما يكون PATH فيها مختصراً (مثل مجلد venv فقط) فلا يجد
shutil.which الأداة رغم أنها مثبتة في /usr/bin. لذلك نبحث بالترتيب: متغير الإعداد (FFMPEG_BIN / FFPROBE_BIN)،
ثم PATH، ثم المجلدات المعتادة.
"""
import os
import shutil
from pathlib import Path
from typing import Optional

_FALLBACK_DIRS = ("/usr/bin", "/usr/local/bin", "/snap/bin", "/opt/homebrew/bin")
_ENV_NAMES = {"ffmpeg": "FFMPEG_BIN", "ffprobe": "FFPROBE_BIN"}


def find_tool(name: str) -> Optional[str]:
    """المسار الكامل للأداة (ffmpeg أو ffprobe)، أو None إن لم توجد."""
    override = os.getenv(_ENV_NAMES.get(name, ""), "").strip()
    if override:
        return override if os.access(override, os.X_OK) else None
    found = shutil.which(name)
    if found:
        return found
    for directory in _FALLBACK_DIRS:
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
