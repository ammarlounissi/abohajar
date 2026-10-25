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
MAX_FACTORY_DURATION = 60.0
MAX_IMAGES = 6                           # أكثر من ذلك يجعل كل لقطة أقصر من أن تُرى
CROSSFADE = 0.5
MAX_BYTES = 10 * 1024 * 1024            # حد عملي لـ 1080p قصير (حد إنستغرام نفسه أعلى بكثير)
MAX_FACTORY_VIDEO_BYTES = 100 * 1024 * 1024
FFMPEG_TIMEOUT = 300

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".wav", ".ogg")
_AUDIO_NAME_RE = re.compile(r"[\w][\w .()-]*")      # اسم ملف فقط: بلا شرطات مائلة ولا ..
AUDIO_FADE_IN, AUDIO_FADE_OUT = 0.5, 1.0

FIT_MODES = ("blur", "crop")             # blur: الصورة كاملة فوق خلفية مموّهة، crop: ملء الإطار بالقص


@dataclass(frozen=True)
class VideoTemplate:
    id: str
    name: str
    duration: float
    max_images: int
    motion: str
    transition: str
    crossfade: float
    card: bool = False


VIDEO_TEMPLATES = (
    VideoTemplate("cinematic_7", "سينمائي قصير", 7, 4, "cinematic", "fade", 0.65),
    VideoTemplate("quick_7", "حيوي سريع قصير", 7, 6, "fast", "smoothleft", 0.2),
    VideoTemplate("detail_7", "تفاصيل المنتج", 7, 4, "detail", "fadeblack", 0.3),
    VideoTemplate("cinematic_15", "سينمائي متوسط", 15, 8, "cinematic", "fade", 0.65),
    VideoTemplate("dynamic_15", "حيوي متوسط", 15, 9, "fast", "wipeleft", 0.2),
    VideoTemplate("cards_15", "بطاقات المنتج", 15, 8, "showcase", "fade", 0.4, card=True),
    VideoTemplate("cinematic_30", "سينمائي طويل", 30, 12, "cinematic", "fade", 0.7),
    VideoTemplate("dynamic_30", "حيوي طويل", 30, 15, "fast", "smoothleft", 0.2),
    VideoTemplate("showcase_30", "استعراض متنوع", 30, 15, "showcase", "circleopen", 0.35),
    VideoTemplate("cards_30", "بطاقات متعددة", 30, 12, "showcase", "fade", 0.4, card=True),
)
VIDEO_TEMPLATES_BY_ID = {template.id: template for template in VIDEO_TEMPLATES}
DEFAULT_FACTORY_TEMPLATE = "cinematic_30"


def get_video_template(template_id: str) -> VideoTemplate:
    template = VIDEO_TEMPLATES_BY_ID.get(template_id)
    if template is None:
        raise InvalidInputError("قالب الفيديو غير معروف")
    return template


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


def build_filter_graph(
    count: int, duration: float, fit: str, template: Optional[VideoTemplate] = None,
) -> str:
    """يبني filter_complex لعدد count من الصور (مدخل ffmpeg رقم i لكل صورة).
    كل لقطة: تركيب على لوحة 1080×1920، ثم zoompan (تكبير تدريجي)، ثم xfade بين اللقطات."""
    crossfade = template.crossfade if template else CROSSFADE
    clip = (duration + (count - 1) * crossfade) / count
    frames = max(2, round(clip * FPS))
    parts = []
    for i in range(count):
        if fit == "blur":
            if template and template.card:
                foreground = (
                    f"[fg{i}]scale={CANVAS_W - 160}:{CANVAS_H - 160}:"
                    f"force_original_aspect_ratio=decrease,"
                    f"pad=iw+80:ih+80:40:40:white[f{i}]"
                )
            else:
                foreground = (
                    f"[fg{i}]scale={CANVAS_W}:{CANVAS_H}:"
                    f"force_original_aspect_ratio=decrease[f{i}]"
                )
            compose = (
                f"[{i}:v]split=2[bg{i}][fg{i}];"
                f"[bg{i}]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H},boxblur=30:5[b{i}];"
                f"{foreground};"
                f"[b{i}][f{i}]overlay=(W-w)/2:(H-h)/2,setsar=1[c{i}]"
            )
        else:
            compose = (
                f"[{i}:v]scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=increase,"
                f"crop={CANVAS_W}:{CANVAS_H},setsar=1[c{i}]"
            )
        parts.append(compose)
        motion = template.motion if template else "cinematic"
        amount = {"cinematic": 0.08, "fast": 0.18, "detail": 0.25, "showcase": 0.12}[motion]
        step = amount / frames
        zoom = (
            f"min(1.0+{step:.6f}*on,{1 + amount:.2f})"
            if i % 2 == 0 else f"max({1 + amount:.2f}-{step:.6f}*on,1.0)"
        )
        if motion in ("fast", "showcase") and i % 2 == 0:
            x_position = f"max(0,min(iw-iw/zoom,(iw-iw/zoom)*on/{frames}))"
        elif motion in ("fast", "showcase"):
            x_position = f"max(0,min(iw-iw/zoom,(iw-iw/zoom)*(1-on/{frames})))"
        else:
            x_position = "iw/2-(iw/zoom/2)"
        parts.append(
            f"[c{i}]zoompan=z='{zoom}':x='{x_position}':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},format=yuv420p,setsar=1[v{i}]"
        )

    if count == 1:
        parts.append("[v0]null[out]")
    else:
        last = "v0"
        for i in range(1, count):
            offset = i * (clip - crossfade)
            out = "out" if i == count - 1 else f"x{i}"
            parts.append(
                f"[{last}][v{i}]xfade=transition={template.transition if template else 'fade'}:"
                f"duration={crossfade}:offset={offset:.3f}[{out}]"
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
    audio: Optional[Path] = None, template: Optional[VideoTemplate] = None,
) -> None:
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    for image in images:
        command += ["-i", str(image)]
    graph = build_filter_graph(len(images), duration, fit, template)
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


def render_video_file(
    images: List[Path], output: Path, *, duration: float, fit: str = "blur",
    audio: Optional[Path], max_bytes: int, template: Optional[VideoTemplate] = None,
) -> int:
    """يرندر صوراً إلى ملف مؤقت، ويعيد المحاولة بجودة أقل عند تجاوز حد الحجم."""
    if not images:
        raise NotFoundError("لا توجد صور لبناء الفيديو")
    if not (MIN_DURATION <= duration <= 60):
        raise InvalidInputError("مدة الفيديو يجب أن تكون بين 3 و60 ثانية")

    ffmpeg = ffmpeg_tools.find_tool("ffmpeg")
    if not ffmpeg:
        raise VideoToolMissingError(
            "ffmpeg غير موجود للخدمة. ثبّته (sudo apt install ffmpeg) أو حدّد مساره في .env: FFMPEG_BIN=/usr/bin/ffmpeg"
        )
    _run_ffmpeg(ffmpeg, images, duration, fit, 23, output, audio, template)
    if output.stat().st_size > max_bytes:
        logger.info("Video is %d bytes, re-encoding smaller", output.stat().st_size)
        _run_ffmpeg(ffmpeg, images, duration, fit, 28, output, audio, template)
    size = output.stat().st_size
    if size > max_bytes:
        raise VideoRenderError("حجم الفيديو الناتج أكبر من المسموح حتى بعد الضغط")
    return size


def create_product_video(
    sku: str, *, duration: float = DEFAULT_DURATION, fit: str = "blur", audio: Optional[str] = None,
    factory_id: Optional[int] = None,
) -> VideoResult:
    return _create_product_video(
        sku, duration=duration, fit=fit, audio=audio, factory_id=factory_id,
        max_duration=MAX_DURATION, max_bytes=MAX_BYTES,
    )


def create_factory_product_video(
    sku: str, *, duration: float, audio: Optional[str], factory_id: int,
    template: VideoTemplate,
) -> VideoResult:
    """يبني فيديو مصنع حتى دقيقة ويحفظه في مجلد المنتج."""
    return _create_product_video(
        sku, duration=duration, fit="blur", audio=audio, factory_id=factory_id,
        max_duration=MAX_FACTORY_DURATION, max_bytes=MAX_FACTORY_VIDEO_BYTES,
        template=template,
    )


def _create_product_video(
    sku: str, *, duration: float, fit: str, audio: Optional[str],
    factory_id: Optional[int], max_duration: float, max_bytes: int,
    template: Optional[VideoTemplate] = None,
) -> VideoResult:
    """يبني فيديو جديداً في آخر تسلسل فيديوهات المنتج ({SKU}/{n}.mp4) دون المساس بأي فيديو قائم.
    لا يلمس قاعدة البيانات. NotFoundError: لا صور للمنتج. InvalidInputError: مدخل غير صالح.
    VideoToolMissingError / VideoRenderError: مشاكل ffmpeg."""
    if not media.is_valid_media_sku(sku):
        raise InvalidInputError("رمز المنتج (SKU) غير صالح")
    if fit not in FIT_MODES:
        raise InvalidInputError(f"طريقة الملاءمة يجب أن تكون إحدى: {', '.join(FIT_MODES)}")
    if not (MIN_DURATION <= duration <= max_duration):
        raise InvalidInputError(f"مدة الفيديو يجب أن تكون بين {MIN_DURATION:g} و{max_duration:g} ثانية")
    if media.media_root_for(factory_id) is None:
        raise InvalidInputError("MEDIA_ROOT غير مضبوط")

    audio_path = resolve_audio(audio) if audio else None

    images = list_source_images(sku, factory_id)[:template.max_images if template else MAX_IMAGES]
    if not images:
        raise NotFoundError("لا توجد صور لهذا المنتج لبناء فيديو منها")

    folder = media.product_dir(sku, factory_id)
    # نبني في ملف مؤقت ثم ننشره باسمه النهائي، فلا يرى Nginx ولا Syncthing ملفاً ناقصاً (.tmp مُتجاهَل)
    tmp = folder / f".video-{uuid.uuid4().hex}.tmp"
    try:
        size = render_video_file(
            images, tmp, duration=duration, fit=fit, audio=audio_path,
            max_bytes=max_bytes, template=template,
        )
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
