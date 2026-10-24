"""توليد ريلز قصير (MP4 عمودي) من صور المنتج باستعمال ffmpeg.

المدخل: صور المنتج الموجودة على القرص  {MEDIA_ROOT}/{الفئة}/{SKU}/{1,2,3...}.{jpg|png|webp}
المخرج: {MEDIA_ROOT}/{الفئة}/{SKU}/1.mp4   ويُخدم عامّاً بالرابط  {IMAGE_BASE_URL}/{SKU}/1.mp4
        (قاعدة Nginx الحالية تسمح بـ [0-9]+\\.mp4 فلا حاجة لتعديلها).

المواصفات (من وثيقة المشروع 4.4): MP4 (H.264)، 9:16 بدقة 720×1280، حوالي 7 ثوانٍ، بدون صوت،
حجم أقل من 3 ميغابايت، `-movflags +faststart`. الغلاف (poster) هو الصورة الرئيسية 1.jpg نفسها
لأنها أول لقطة في الفيديو، فلا ملف إضافي.

الخدمة لا تعرف HTTP ولا أي قناة: تُرجع نتيجة (VideoResult) وترفع أخطاء الخدمات.
الكتابة على القرص فقط. ربط الرابط بالمنتج في القاعدة في attach_video (products.py).
"""
import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from app.core.config import settings
from app.services import media
from app.services.errors import InvalidInputError, NotFoundError, ServiceError

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 720, 1280
CANVAS_W, CANVAS_H = 1080, 1920          # نركّب اللقطة بدقة أعلى ثم نصغّرها: حركة التكبير أنعم
FPS = 25
VIDEO_INDEX = 1                          # اسم الملف الناتج 1.mp4
VIDEO_EXT = ".mp4"

DEFAULT_DURATION = 7.0
MIN_DURATION, MAX_DURATION = 3.0, 15.0
MAX_IMAGES = 6                           # أكثر من ذلك يجعل كل لقطة أقصر من أن تُرى
CROSSFADE = 0.5
MAX_BYTES = 3 * 1024 * 1024
FFMPEG_TIMEOUT = 300

FIT_MODES = ("blur", "crop")             # blur: الصورة كاملة فوق خلفية مموّهة، crop: ملء الإطار بالقص


class VideoToolMissingError(ServiceError):
    """ffmpeg غير مثبت على الخادم."""


class VideoRenderError(ServiceError):
    """فشل ffmpeg في بناء الفيديو."""


@dataclass
class VideoResult:
    sku: str
    path: Path
    url: str
    size_bytes: int
    duration: float
    images_used: int
    replaced: bool


def public_video_url(sku: str, index: int = VIDEO_INDEX) -> str:
    return f"{settings.IMAGE_BASE_URL}/{sku}/{index}{VIDEO_EXT}"


def video_path(sku: str, index: int = VIDEO_INDEX) -> Path:
    return media.product_dir(sku) / f"{index}{VIDEO_EXT}"


def has_video(sku: str) -> bool:
    return video_path(sku).is_file()


def list_source_images(sku: str) -> List[Path]:
    """صور المنتج بترتيب أرقامها (1 ثم 2 ...). ملف واحد لكل رقم، وبلا ملفات مخفية أو تعارضات مزامنة."""
    folder = media.product_dir(sku)
    if not folder.is_dir():
        return []
    by_index: dict = {}
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.name.startswith(".") or ".sync-conflict-" in f.name:
            continue
        if not f.stem.isascii() or not f.stem.isdigit() or int(f.stem) == 0:
            continue
        if f.suffix.lower() not in media.IMAGE_EXTENSIONS:
            continue
        by_index.setdefault(int(f.stem), f)
    return [by_index[i] for i in sorted(by_index)]


def _clip_seconds(duration: float, count: int) -> float:
    """مدة كل لقطة بحيث يكون المجموع بعد خصم تداخل الانتقالات = duration."""
    return (duration + (count - 1) * CROSSFADE) / count


def build_filter_graph(count: int, duration: float, fit: str) -> str:
    """يبني filter_complex لعدد count من الصور (مدخل ffmpeg رقم i لكل صورة).
    كل لقطة: تركيب على لوحة 1080×1920، ثم zoompan (تكبير تدريجي)، ثم xfade بين اللقطات."""
    clip = _clip_seconds(duration, count)
    frames = max(2, round(clip * FPS))
    parts = []
    for i in range(count):
        if fit == "blur":
            compose = (
                f"[{i}:v]split=2[bg{i}][fg{i}];"
                f"[bg{i}]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H},boxblur=30:5[b{i}];"
                f"[fg{i}]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=decrease[f{i}];"
                f"[b{i}][f{i}]overlay=(W-w)/2:(H-h)/2,setsar=1[c{i}]"
            )
        else:
            compose = (
                f"[{i}:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H},setsar=1[c{i}]"
            )
        parts.append(compose)
        # تكبير من 1.0 إلى 1.10 على مدى اللقطة؛ اللقطات الزوجية تبدأ مكبّرة وتتراجع لتنويع الحركة
        step = 0.10 / frames
        zoom = f"min(1.0+{step:.6f}*on,1.10)" if i % 2 == 0 else f"max(1.10-{step:.6f}*on,1.0)"
        parts.append(
            f"[c{i}]zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},format=yuv420p,setsar=1[v{i}]"
        )

    if count == 1:
        parts.append("[v0]null[out]")
    else:
        last = "v0"
        for i in range(1, count):
            offset = i * (clip - CROSSFADE)
            out = "out" if i == count - 1 else f"x{i}"
            parts.append(
                f"[{last}][v{i}]xfade=transition=fade:duration={CROSSFADE}:offset={offset:.3f}[{out}]"
            )
            last = out
    return ";".join(parts)


def _encode_args(crf: int) -> list:
    return [
        "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
        "-maxrate", "2200k", "-bufsize", "4400k",
        "-pix_fmt", "yuv420p", "-profile:v", "main", "-an",
        "-movflags", "+faststart", "-f", "mp4",
    ]


def _run_ffmpeg(ffmpeg: str, images: List[Path], duration: float, fit: str, crf: int, output: Path) -> None:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for image in images:
        command += ["-i", str(image)]
    command += [
        "-filter_complex", build_filter_graph(len(images), duration, fit),
        "-map", "[out]", "-t", f"{duration:.2f}", *_encode_args(crf), str(output),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise VideoRenderError("استغرق بناء الفيديو وقتاً أطول من المسموح") from exc
    if result.returncode:
        lines = (result.stderr or "").strip().splitlines()
        logger.error("ffmpeg failed (exit %s): %s", result.returncode, " | ".join(lines[-3:]))
        raise VideoRenderError("تعذّر بناء الفيديو من الصور (راجع سجل الخادم)")


def _archive_existing(sku: str, path: Path) -> bool:
    """الفيديو القديم يُنقل إلى MEDIA_BACKUP_DIR بدل حذفه (كما تفعل الصور)."""
    if not path.is_file():
        return False
    backup_dir = settings.MEDIA_BACKUP_DIR / sku
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.move(str(path), str(backup_dir / f"{path.stem}_{stamp}{path.suffix}"))
    return True


def create_product_video(
    sku: str, *, duration: float = DEFAULT_DURATION, fit: str = "blur", overwrite: bool = False,
) -> VideoResult:
    """يبني {SKU}/1.mp4 من صور المنتج. لا يلمس قاعدة البيانات.
    NotFoundError: لا صور للمنتج. InvalidInputError: SKU أو مدخل غير صالح، أو فيديو موجود ولم يُطلب الاستبدال.
    VideoToolMissingError / VideoRenderError: مشاكل ffmpeg."""
    if not media.is_valid_media_sku(sku):
        raise InvalidInputError("رمز المنتج (SKU) غير صالح")
    if fit not in FIT_MODES:
        raise InvalidInputError(f"طريقة الملاءمة يجب أن تكون إحدى: {', '.join(FIT_MODES)}")
    if not (MIN_DURATION <= duration <= MAX_DURATION):
        raise InvalidInputError(f"مدة الفيديو يجب أن تكون بين {MIN_DURATION:g} و{MAX_DURATION:g} ثانية")
    if settings.MEDIA_ROOT is None:
        raise InvalidInputError("MEDIA_ROOT غير مضبوط")

    images = list_source_images(sku)[:MAX_IMAGES]
    if not images:
        raise NotFoundError("لا توجد صور لهذا المنتج لبناء فيديو منها")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise VideoToolMissingError("ffmpeg غير مثبت على الخادم (sudo apt install ffmpeg)")

    target = video_path(sku)
    if target.exists() and not overwrite:
        raise InvalidInputError("للمنتج فيديو موجود مسبقاً (استعمل الاستبدال لإعادة بنائه)")

    # نبني في ملف مؤقت ثم نستبدل، فلا يرى Nginx ولا Syncthing ملفاً ناقصاً (.tmp مُتجاهَل)
    tmp = target.parent / f".video-{uuid.uuid4().hex}.tmp"
    try:
        _run_ffmpeg(ffmpeg, images, duration, fit, 26, tmp)
        if tmp.stat().st_size > MAX_BYTES:                 # إعادة بجودة أقل مرة واحدة
            logger.info("%s video is %d bytes, re-encoding smaller", sku, tmp.stat().st_size)
            _run_ffmpeg(ffmpeg, images, duration, fit, 31, tmp)
        size = tmp.stat().st_size
        if size > MAX_BYTES:
            raise VideoRenderError("حجم الفيديو الناتج أكبر من المسموح حتى بعد الضغط")
        replaced = _archive_existing(sku, target)
        os.replace(tmp, target)
        target.chmod(0o664)                                # www-data يقرأ، وSyncthing يكتب
    finally:
        tmp.unlink(missing_ok=True)

    logger.info("Created video for %s (%d images, %d bytes, replaced=%s)", sku, len(images), size, replaced)
    return VideoResult(
        sku=sku, path=target, url=public_video_url(sku), size_bytes=size,
        duration=duration, images_used=len(images), replaced=replaced,
    )
