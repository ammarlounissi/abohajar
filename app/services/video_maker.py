"""توليد ريلز قصير (MP4 عمودي) من صور المنتج باستعمال ffmpeg.

المدخل: صور المنتج الموجودة على القرص  {MEDIA_ROOT}/{الفئة}/{SKU}/{1,2,3...}.{jpg|png|webp}
المخرج: {MEDIA_ROOT}/{الفئة}/{SKU}/{n}.mp4   ويُخدم عامّاً بالرابط  {IMAGE_BASE_URL}/{SKU}/{n}.mp4
        (قاعدة Nginx الحالية تسمح بـ [0-9]+\\.mp4 فلا حاجة لتعديلها).
        n = أول رقم بعد آخر فيديو موجود (1 ثم 2 ثم 3...). لا يُستبدل ولا يُحذف أي فيديو قائم أبداً.

المواصفات متوافقة مع ريلز إنستغرام: MP4 (H.264 High، yuv420p)، 9:16 بدقة 1080×1920، 30 إطاراً/ث،
حوالي 7 ثوانٍ (المسموح 3 ثوانٍ فأكثر)، بدون صوت أو بصوت AAC ستيريو 48kHz، `-movflags +faststart`.
الغلاف (poster) هو الصورة الرئيسية 1.jpg نفسها لأنها أول لقطة في الفيديو.

الخدمة لا تعرف HTTP ولا أي قناة: تُرجع نتيجة (VideoResult) وترفع أخطاء الخدمات.
الكتابة على القرص فقط. ربط الرابط بالمنتج في القاعدة في products.generate_product_video.
"""
import logging
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from app.core.config import settings
from app.services import ffmpeg_tools, media
from app.services.errors import InvalidInputError, NotFoundError, ServiceError

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 1080, 1920               # مقاس ريلز إنستغرام الموصى به (9:16)
CANVAS_W, CANVAS_H = 2160, 3840          # نركّب اللقطة بضعف الدقة ثم نصغّرها: حركة التكبير أنعم بلا اهتزاز
FPS = 30
VIDEO_EXT = ".mp4"

DEFAULT_DURATION = 7.0
MIN_DURATION, MAX_DURATION = 3.0, 15.0
MAX_IMAGES = 6                           # أكثر من ذلك يجعل كل لقطة أقصر من أن تُرى
CROSSFADE = 0.5
MAX_BYTES = 10 * 1024 * 1024            # حد عملي لـ 1080p قصير (حد إنستغرام نفسه أعلى بكثير)
FFMPEG_TIMEOUT = 300

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".ogg")
_AUDIO_NAME_RE = re.compile(r"[\w][\w .()-]*")      # اسم ملف فقط: بلا شرطات مائلة ولا ..
AUDIO_FADE_IN, AUDIO_FADE_OUT = 0.5, 1.0

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
    index: int
    audio: Optional[str] = None


def public_video_url(sku: str, index: int, factory_id: Optional[int] = None) -> str:
    return media.public_media_url(sku, f"{index}{VIDEO_EXT}", factory_id)


def video_path(sku: str, index: int, factory_id: Optional[int] = None) -> Path:
    return media.product_dir(sku, factory_id) / f"{index}{VIDEO_EXT}"


def video_indexes(sku: str, factory_id: Optional[int] = None) -> List[int]:
    """أرقام الفيديوهات الموجودة فعلاً (1.mp4 و2.mp4...) بالترتيب، سواء بناها النظام أو رُفعت يدوياً."""
    folder = media.product_dir(sku, factory_id)
    if not folder.is_dir():
        return []
    found = {
        int(f.stem) for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() == VIDEO_EXT and f.stem.isascii() and f.stem.isdigit() and int(f.stem) > 0
    }
    return sorted(found)


def has_video(sku: str, factory_id: Optional[int] = None) -> bool:
    return bool(video_indexes(sku, factory_id))


def list_source_images(sku: str, factory_id: Optional[int] = None) -> List[Path]:
    """صور المنتج بترتيب أرقامها (1 ثم 2 ...). ملف واحد لكل رقم، وبلا ملفات مخفية أو تعارضات مزامنة."""
    folder = media.product_dir(sku, factory_id)
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


def list_audio_files() -> List[str]:
    """أسماء ملفات الصوت المتاحة في AUDIO_ROOT (مرتبة)."""
    root = settings.AUDIO_ROOT
    if root is None or not root.is_dir():
        return []
    return sorted(
        f.name for f in root.iterdir()
        if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in AUDIO_EXTENSIONS
    )


def resolve_audio(name: str) -> Path:
    """اسم ملف صوتي داخل AUDIO_ROOT -> مساره الكامل، مع منع اجتياز المجلد."""
    root = settings.AUDIO_ROOT
    if root is None:
        raise InvalidInputError("AUDIO_ROOT غير مضبوط في الإعدادات")
    if not _AUDIO_NAME_RE.fullmatch(name or "") or ".." in name:
        raise InvalidInputError("اسم الملف الصوتي غير صالح (اسم ملف فقط بلا مسار)")
    if Path(name).suffix.lower() not in AUDIO_EXTENSIONS:
        raise InvalidInputError(f"صيغة الصوت يجب أن تكون إحدى: {', '.join(AUDIO_EXTENSIONS)}")
    path = (root.resolve() / name).resolve()
    if path.parent != root.resolve() or not path.is_file():
        raise NotFoundError(f"الملف الصوتي غير موجود في مجلد الصوتيات: {name}")
    return path


def _encode_args(crf: int, with_audio: bool = False) -> list:
    audio = ["-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"] if with_audio else ["-an"]
    return [
        "-c:v", "libx264", "-preset", "slow", "-crf", str(crf),
        "-maxrate", "6000k", "-bufsize", "12000k",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.0", "-r", str(FPS), *audio,
        "-movflags", "+faststart", "-f", "mp4",
    ]


def _run_ffmpeg(
    ffmpeg: str, images: List[Path], duration: float, fit: str, crf: int, output: Path,
    audio: Optional[Path] = None,
) -> None:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for image in images:
        command += ["-i", str(image)]
    graph = build_filter_graph(len(images), duration, fit)
    maps = ["-map", "[out]"]
    if audio is not None:
        # الصوت يتكرر إن كان أقصر من الفيديو ويُقص عند نهايته (-t)، مع ظهور تدريجي واختفاء تدريجي
        command += ["-stream_loop", "-1", "-i", str(audio)]
        fade_out_start = max(0.0, duration - AUDIO_FADE_OUT)
        graph += (
            f";[{len(images)}:a]afade=t=in:st=0:d={AUDIO_FADE_IN},"
            f"afade=t=out:st={fade_out_start:.2f}:d={AUDIO_FADE_OUT}[aout]"
        )
        maps += ["-map", "[aout]"]
    command += [
        "-filter_complex", graph,
        *maps, "-t", f"{duration:.2f}", *_encode_args(crf, audio is not None), str(output),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise VideoRenderError("استغرق بناء الفيديو وقتاً أطول من المسموح") from exc
    if result.returncode:
        lines = (result.stderr or "").strip().splitlines()
        logger.error("ffmpeg failed (exit %s): %s", result.returncode, " | ".join(lines[-3:]))
        raise VideoRenderError("تعذّر بناء الفيديو من الصور (راجع سجل الخادم)")


def create_product_video(
    sku: str, *, duration: float = DEFAULT_DURATION, fit: str = "blur", audio: Optional[str] = None,
    factory_id: Optional[int] = None,
) -> VideoResult:
    """يبني فيديو جديداً في آخر تسلسل فيديوهات المنتج ({SKU}/{n}.mp4) دون المساس بأي فيديو قائم.
    لا يلمس قاعدة البيانات. NotFoundError: لا صور للمنتج. InvalidInputError: مدخل غير صالح.
    VideoToolMissingError / VideoRenderError: مشاكل ffmpeg."""
    if not media.is_valid_media_sku(sku):
        raise InvalidInputError("رمز المنتج (SKU) غير صالح")
    if fit not in FIT_MODES:
        raise InvalidInputError(f"طريقة الملاءمة يجب أن تكون إحدى: {', '.join(FIT_MODES)}")
    if not (MIN_DURATION <= duration <= MAX_DURATION):
        raise InvalidInputError(f"مدة الفيديو يجب أن تكون بين {MIN_DURATION:g} و{MAX_DURATION:g} ثانية")
    if media.media_root_for(factory_id) is None:
        raise InvalidInputError("MEDIA_ROOT غير مضبوط")

    audio_path = resolve_audio(audio) if audio else None

    images = list_source_images(sku, factory_id)[:MAX_IMAGES]
    if not images:
        raise NotFoundError("لا توجد صور لهذا المنتج لبناء فيديو منها")

    ffmpeg = ffmpeg_tools.find_tool("ffmpeg")
    if not ffmpeg:
        raise VideoToolMissingError(
            "ffmpeg غير موجود للخدمة. ثبّته (sudo apt install ffmpeg) أو حدّد مساره في .env: FFMPEG_BIN=/usr/bin/ffmpeg"
        )

    folder = media.product_dir(sku, factory_id)
    # نبني في ملف مؤقت ثم ننشره باسمه النهائي، فلا يرى Nginx ولا Syncthing ملفاً ناقصاً (.tmp مُتجاهَل)
    tmp = folder / f".video-{uuid.uuid4().hex}.tmp"
    try:
        _run_ffmpeg(ffmpeg, images, duration, fit, 23, tmp, audio_path)
        if tmp.stat().st_size > MAX_BYTES:                 # إعادة بجودة أقل مرة واحدة
            logger.info("%s video is %d bytes, re-encoding smaller", sku, tmp.stat().st_size)
            _run_ffmpeg(ffmpeg, images, duration, fit, 28, tmp, audio_path)
        size = tmp.stat().st_size
        if size > MAX_BYTES:
            raise VideoRenderError("حجم الفيديو الناتج أكبر من المسموح حتى بعد الضغط")
        index, target = _publish(sku, tmp, factory_id)
    finally:
        tmp.unlink(missing_ok=True)

    logger.info("Created video %s/%d.mp4 (%d images, %d bytes)", sku, index, len(images), size)
    return VideoResult(
        sku=sku, path=target, url=public_video_url(sku, index, factory_id), size_bytes=size,
        duration=duration, images_used=len(images), index=index, audio=audio,
    )


def _publish(sku: str, tmp: Path, factory_id: Optional[int] = None) -> tuple:
    """ينشر الملف المؤقت باسم {أول رقم فارغ بعد آخر فيديو}.mp4. os.link يفشل إن وُجد الهدف،
    فلا يستبدل أي ملف حتى لو بنى طلبان متزامنان فيديو للمنتج نفسه (يعيد المحاولة برقم أعلى)."""
    for _ in range(10):
        indexes = video_indexes(sku, factory_id)
        index = (indexes[-1] if indexes else 0) + 1
        target = video_path(sku, index, factory_id)
        try:
            os.link(tmp, target)
        except FileExistsError:
            continue
        target.chmod(0o664)                                # www-data يقرأ، وSyncthing يكتب
        return index, target
    raise VideoRenderError("تعذّر حجز رقم للفيديو الجديد، أعد المحاولة")
