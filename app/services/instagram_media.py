"""Prepare publicly accessible JPEG images that meet Instagram feed requirements."""
import asyncio
import hashlib
import io
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.config import settings
from app.models import ReportKind
from app.services.social.base import PublishError, is_http_url

MIN_ASPECT_RATIO = 4 / 5
MAX_ASPECT_RATIO = 1.91
MIN_WIDTH = 320
MAX_WIDTH = 1440
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 50_000_000
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
MAX_VIDEO_BYTES = 1024 * 1024 * 1024


def _fit_image(content: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(content)) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise PublishError(ReportKind.MEDIA, "instagram_image_dimensions_too_large")
            source.seek(0)
            image = ImageOps.exif_transpose(source).convert("RGB")
    except PublishError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise PublishError(ReportKind.MEDIA, "instagram_image_invalid_or_unsupported") from exc

    width, height = image.size
    ratio = width / height
    if ratio < MIN_ASPECT_RATIO:
        crop_height = int(width / MIN_ASPECT_RATIO)
        top = (height - crop_height) // 2
        image = image.crop((0, top, width, top + crop_height))
    elif ratio > MAX_ASPECT_RATIO:
        crop_width = int(height * MAX_ASPECT_RATIO)
        left = (width - crop_width) // 2
        image = image.crop((left, 0, left + crop_width, height))

    width, height = image.size
    if width < MIN_WIDTH:
        image = image.resize((MIN_WIDTH, round(height * MIN_WIDTH / width)), Image.Resampling.LANCZOS)
    elif width > MAX_WIDTH:
        image = image.resize((MAX_WIDTH, round(height * MAX_WIDTH / width)), Image.Resampling.LANCZOS)

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=90, optimize=True)
    return output.getvalue()


def _store_derivative(content: bytes) -> str:
    if not settings.MEDIA_ROOT or not settings.BASE_URL:
        raise PublishError(ReportKind.UNKNOWN, "config_missing:MEDIA_ROOT_or_BASE_URL")

    filename = f"{hashlib.sha256(content).hexdigest()}.jpg"
    directory = settings.MEDIA_ROOT / ".instagram"
    path = directory / filename
    try:
        if not path.is_file():
            directory.mkdir(parents=True, exist_ok=True)
            temporary = directory / f".{uuid.uuid4().hex}.tmp"
            try:
                temporary.write_bytes(content)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    except OSError as exc:
        raise PublishError(
            ReportKind.UNKNOWN, f"instagram_image_cache_failed:{type(exc).__name__}",
        ) from exc
    return f"{settings.BASE_URL.rstrip('/')}/api/media/instagram/{filename}"


def _run_media_command(command: list[str], timeout: int, failure_code: str) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise PublishError(ReportKind.UNKNOWN, "instagram_video_tools_missing:ffmpeg_ffprobe") from exc
    except subprocess.TimeoutExpired as exc:
        raise PublishError(ReportKind.MEDIA, f"{failure_code}_timeout") from exc
    if result.returncode:
        details = (result.stderr or "").strip().splitlines()
        detail = details[-1][:200] if details else f"exit_{result.returncode}"
        raise PublishError(ReportKind.MEDIA, f"{failure_code}:{detail}")
    return result


def _video_dimensions(path: Path) -> tuple[int, int]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise PublishError(ReportKind.UNKNOWN, "instagram_video_tools_missing:ffprobe")
    result = _run_media_command(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path),
        ],
        timeout=30,
        failure_code="instagram_video_probe_failed",
    )
    try:
        width, height = (int(value) for value in result.stdout.strip().split("x", 1))
    except (TypeError, ValueError) as exc:
        raise PublishError(ReportKind.MEDIA, "instagram_video_dimensions_unavailable") from exc
    if width < 1 or height < 1:
        raise PublishError(ReportKind.MEDIA, "instagram_video_dimensions_invalid")
    return width, height


def _transcode_reel(source: Path, output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise PublishError(ReportKind.UNKNOWN, "instagram_video_tools_missing:ffmpeg")

    _run_media_command(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-movflags", "+faststart", "-f", "mp4", str(output),
        ],
        timeout=900,
        failure_code="instagram_video_transcode_failed",
    )


def _store_video_derivative(path: Path) -> str:
    if not settings.MEDIA_ROOT or not settings.BASE_URL:
        raise PublishError(ReportKind.UNKNOWN, "config_missing:MEDIA_ROOT_or_BASE_URL")

    digest = hashlib.sha256()
    try:
        with path.open("rb") as video:
            for chunk in iter(lambda: video.read(1024 * 1024), b""):
                digest.update(chunk)
        filename = f"{digest.hexdigest()}.mp4"
        destination = settings.MEDIA_ROOT / ".instagram" / filename
        if not destination.is_file():
            os.replace(path, destination)
    except OSError as exc:
        raise PublishError(
            ReportKind.UNKNOWN, f"instagram_video_cache_failed:{type(exc).__name__}",
        ) from exc
    return f"{settings.BASE_URL.rstrip('/')}/api/media/instagram/{filename}"


async def prepare_instagram_video(
    source_url: str,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> str:
    """Download an MP4, center-crop it to a 1080x1920 reel, and cache the derivative."""
    if not is_http_url(source_url):
        raise PublishError(ReportKind.MEDIA, "media_url_not_http")
    if not settings.MEDIA_ROOT or not settings.BASE_URL:
        raise PublishError(ReportKind.UNKNOWN, "config_missing:MEDIA_ROOT_or_BASE_URL")

    directory = settings.MEDIA_ROOT / ".instagram"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        source = directory / f".{uuid.uuid4().hex}.source.mp4"
        output = directory / f".{uuid.uuid4().hex}.output.mp4"
    except OSError as exc:
        raise PublishError(
            ReportKind.UNKNOWN, f"instagram_video_cache_failed:{type(exc).__name__}",
        ) from exc

    try:
        try:
            async with httpx.AsyncClient(
                timeout=60.0, transport=transport, follow_redirects=True,
            ) as client:
                async with client.stream("GET", source_url) as response:
                    if response.status_code >= 400:
                        raise PublishError(
                            ReportKind.MEDIA, f"instagram_video_download_http_{response.status_code}",
                        )
                    size = 0
                    with source.open("wb") as video:
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_VIDEO_BYTES:
                                raise PublishError(ReportKind.MEDIA, "instagram_video_file_too_large")
                            video.write(chunk)
        except PublishError:
            raise
        except httpx.HTTPError as exc:
            raise PublishError(
                ReportKind.NETWORK, f"instagram_video_download_failed:{type(exc).__name__}",
            ) from exc
        except OSError as exc:
            raise PublishError(
                ReportKind.UNKNOWN, f"instagram_video_cache_failed:{type(exc).__name__}",
            ) from exc

        dimensions = await asyncio.to_thread(_video_dimensions, source)
        if dimensions == (REEL_WIDTH, REEL_HEIGHT):
            return source_url
        await asyncio.to_thread(_transcode_reel, source, output)
        return await asyncio.to_thread(_store_video_derivative, output)
    finally:
        source.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


async def prepare_instagram_image(
    source_url: str,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> str:
    """Download, crop/resize and cache an image; return its public derivative URL."""
    if not is_http_url(source_url):
        raise PublishError(ReportKind.MEDIA, "media_url_not_http")

    try:
        async with httpx.AsyncClient(
            timeout=30.0, transport=transport, follow_redirects=True,
        ) as client:
            async with client.stream("GET", source_url) as response:
                if response.status_code >= 400:
                    raise PublishError(
                        ReportKind.MEDIA, f"instagram_image_download_http_{response.status_code}",
                    )
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > MAX_DOWNLOAD_BYTES:
                        raise PublishError(ReportKind.MEDIA, "instagram_image_file_too_large")
    except PublishError:
        raise
    except httpx.HTTPError as exc:
        raise PublishError(ReportKind.NETWORK, f"instagram_image_download_failed:{type(exc).__name__}") from exc

    fitted = await asyncio.to_thread(_fit_image, bytes(content))
    return _store_derivative(fitted)


def derivative_path(filename: str) -> Optional[Path]:
    """Resolve only hash-named Instagram media; never accept arbitrary filesystem paths."""
    suffix = Path(filename).suffix
    if suffix not in (".jpg", ".mp4") or len(filename) != 64 + len(suffix):
        return None
    digest = filename[:-len(suffix)]
    if any(character not in "0123456789abcdef" for character in digest):
        return None
    if not settings.MEDIA_ROOT:
        return None
    path = settings.MEDIA_ROOT / ".instagram" / filename
    return path if path.is_file() else None
