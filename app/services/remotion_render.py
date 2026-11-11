"""رندرة فيديو الزبون بـ Remotion: المكوّن نفسه (ProductReel) الذي يراه الزبون في معاينة اللوحة.

المكوّن وسكريبت الرندرة في مشروع الواجهة (REMOTION_DIR = مجلد ikhlasdz على الجهاز نفسه):
    {REMOTION_DIR}/remotion/render.mjs   يقرأ طلباً بصيغة JSON من stdin ويكتب MP4.

نضع الصور والصوت في مجلد مؤقت، والسكريبت يخدمه عبر خادم محلي (127.0.0.1) يقرأ منه Chrome،
ثم يرندر بـ @remotion/renderer. بعده ننقل فهرس MP4 إلى أوله (faststart) بـ ffmpeg دون إعادة ترميز.

الواجهة نفسها كـ video_maker.render_video_file كي يختار العامل المحرك دون أن يعرف تفاصيله."""
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from app.core.config import settings
from app.services import ffmpeg_tools, video_maker
from app.services.errors import InvalidInputError, NotFoundError

logger = logging.getLogger(__name__)

RENDER_SCRIPT = Path("remotion") / "render.mjs"
COMPOSITION_ID = "ProductReel"
RENDER_TIMEOUT = 900          # ثوانٍ: التجميع (bundle) مع رندرة 30 ثانية على سيرفر يشاركه Ollama
CRF_STEPS = (23, 28)          # الثانية فقط إن تجاوز الناتج الحد


def render_props(
    images: List[str], audio: Optional[str], *, duration: float, template: video_maker.VideoTemplate,
) -> dict:
    """خصائص ProductReel. الصور والصوت روابط أو أسماء ملفات في مجلد الأصول (يحوّلها السكريبت إلى روابط)."""
    return {
        "images": images,
        "audio": audio,
        "duration": duration,
        **video_maker.template_spec(template),
    }


def _link_or_copy(source: Path, target: Path) -> None:
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


def _faststart(source: Path, output: Path) -> None:
    ffmpeg = ffmpeg_tools.find_tool("ffmpeg")
    if not ffmpeg:
        shutil.copyfile(source, output)
        return
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
         "-map", "0", "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(output)],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode:
        logger.warning("faststart remux failed, keeping Remotion output: %s", (result.stderr or "").strip()[-300:])
        shutil.copyfile(source, output)


def _run_render(job: dict, cwd: Path) -> None:
    command = [settings.NODE_BIN, str(RENDER_SCRIPT)]
    try:
        result = subprocess.run(
            command, cwd=cwd, input=json.dumps(job), capture_output=True, text=True,
            timeout=RENDER_TIMEOUT, check=False, preexec_fn=video_maker._lower_priority,
        )
    except FileNotFoundError as exc:
        raise video_maker.VideoToolMissingError(
            f"Node غير موجود للخدمة ({settings.NODE_BIN}). ثبّته أو حدّد مساره في .env: NODE_BIN=/usr/bin/node"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise video_maker.VideoRenderError("استغرق بناء الفيديو وقتاً أطول من المسموح") from exc
    if result.returncode:
        lines = (result.stderr or result.stdout or "").strip().splitlines()
        logger.error("Remotion render failed (exit %s): %s", result.returncode, " | ".join(lines[-5:]))
        raise video_maker.VideoRenderError("تعذّر بناء الفيديو من الصور (راجع سجل العامل)")


def render_video_file(
    images: List[Path], output: Path, *, duration: float, fit: str = "blur",
    audio: Optional[Path], max_bytes: int, template: Optional[video_maker.VideoTemplate] = None,
) -> int:
    """يرندر الصور بقالب ProductReel إلى output ويعيد حجمه. fit موجود للتوافق فقط (القالب يموّه الخلفية دائماً)."""
    if not images:
        raise NotFoundError("لا توجد صور لبناء الفيديو")
    if template is None:
        raise InvalidInputError("قالب الفيديو مطلوب لرندرة Remotion")
    if not (video_maker.MIN_DURATION <= duration <= video_maker.MAX_FACTORY_DURATION):
        raise InvalidInputError("مدة الفيديو يجب أن تكون بين 3 و60 ثانية")
    root = settings.REMOTION_DIR
    if root is None or not (root / RENDER_SCRIPT).is_file():
        raise video_maker.VideoToolMissingError(
            "مشروع Remotion غير مضبوط. حدّد في .env: REMOTION_DIR=<مجلد ikhlasdz> (فيه remotion/render.mjs)"
        )

    with tempfile.TemporaryDirectory(prefix="reel-") as tmp:
        work = Path(tmp)
        assets = work / "assets"
        assets.mkdir()
        names = []
        for index, image in enumerate(images):
            name = f"{index}{image.suffix.lower()}"
            _link_or_copy(image, assets / name)
            names.append(name)
        audio_name = None
        if audio is not None:
            audio_name = f"audio{audio.suffix.lower()}"
            _link_or_copy(audio, assets / audio_name)

        rendered = work / "render.mp4"
        for crf in CRF_STEPS:
            job = {
                "composition": COMPOSITION_ID,
                "assets_dir": str(assets),
                "work_dir": str(work),
                "output": str(rendered),
                "crf": crf,
                "concurrency": settings.REMOTION_CONCURRENCY,
                "props": render_props(names, audio_name, duration=duration, template=template),
            }
            _run_render(job, root)
            if rendered.stat().st_size <= max_bytes:
                break
            logger.info("Remotion video is %d bytes at crf %d, re-rendering smaller", rendered.stat().st_size, crf)
        _faststart(rendered, output)

    size = output.stat().st_size
    if size > max_bytes:
        raise video_maker.VideoRenderError("حجم الفيديو الناتج أكبر من المسموح حتى بعد الضغط")
    return size
