"""اختبارات توليد الريلز بـ ffmpeg. تعمل ffmpeg فعلاً (وتُتخطى إن لم يكن مثبتاً)."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PIL import Image

from app.core.config import settings
from app.services import video_maker
from app.services.errors import InvalidInputError, NotFoundError

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_image(path: Path, size, color="red") -> None:
    Image.new("RGB", size, color).save(path)


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


class VideoMakerBase(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "media"
        self.backup = Path(tmp.name) / "backup"
        self.root.mkdir()
        for name, value in (
            ("MEDIA_ROOT", self.root), ("MEDIA_BACKUP_DIR", self.backup),
            ("IMAGE_BASE_URL", "https://example.com"),
        ):
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.folder = self.root / "B" / "B001"
        self.folder.mkdir(parents=True)
        self.audio_root = Path(tmp.name) / "audio"
        self.audio_root.mkdir()
        patcher = mock.patch.object(settings, "AUDIO_ROOT", self.audio_root)
        patcher.start()
        self.addCleanup(patcher.stop)


class FilterGraphTests(unittest.TestCase):
    def test_single_image_has_no_crossfade(self):
        graph = video_maker.build_filter_graph(1, 7.0, "crop")
        self.assertNotIn("xfade", graph)
        self.assertIn("[out]", graph)

    def test_crossfade_offsets_fill_duration(self):
        graph = video_maker.build_filter_graph(3, 7.0, "blur")
        self.assertEqual(graph.count("xfade"), 2)
        clip = video_maker._clip_seconds(7.0, 3)
        # آخر لقطة تنتهي عند المدة المطلوبة تماماً
        self.assertAlmostEqual(2 * (clip - video_maker.CROSSFADE) + clip, 7.0, places=6)


class SourceImageTests(VideoMakerBase):
    def test_lists_numbered_images_in_order_and_ignores_junk(self):
        for name in ("2.png", "1.jpg", "10.webp", "0.jpg", "cover.jpg", ".hidden.jpg", "3.jpg.tmp",
                     "4.sync-conflict-20260101-AAA.jpg", "1.mp4"):
            (self.folder / name).write_bytes(b"x")
        names = [p.name for p in video_maker.list_source_images("B001")]
        self.assertEqual(names, ["1.jpg", "2.png", "10.webp"])

    def test_missing_folder_gives_empty_list(self):
        self.assertEqual(video_maker.list_source_images("B999"), [])


class ValidationTests(VideoMakerBase):
    def test_invalid_sku(self):
        with self.assertRaises(InvalidInputError):
            video_maker.create_product_video("../etc")

    def test_invalid_fit_and_duration(self):
        make_image(self.folder / "1.jpg", (100, 100))
        with self.assertRaises(InvalidInputError):
            video_maker.create_product_video("B001", fit="stretch")
        with self.assertRaises(InvalidInputError):
            video_maker.create_product_video("B001", duration=1)
        with self.assertRaises(InvalidInputError):
            video_maker.create_product_video("B001", duration=60)

    def test_no_images(self):
        with self.assertRaises(NotFoundError):
            video_maker.create_product_video("B001")

    def test_missing_ffmpeg(self):
        make_image(self.folder / "1.jpg", (100, 100))
        with mock.patch("app.services.video_maker.shutil.which", return_value=None):
            with self.assertRaises(video_maker.VideoToolMissingError):
                video_maker.create_product_video("B001")


class AudioResolveTests(VideoMakerBase):
    def test_lists_only_audio_files(self):
        for name in ("b.mp3", "a.wav", "notes.txt", ".hidden.mp3"):
            (self.audio_root / name).write_bytes(b"x")
        self.assertEqual(video_maker.list_audio_files(), ["a.wav", "b.mp3"])

    def test_rejects_paths_bad_extension_and_missing(self):
        (self.audio_root / "ok.mp3").write_bytes(b"x")
        (self.root / "secret.mp3").write_bytes(b"x")
        for bad in ("../secret.mp3", "/etc/passwd", "sub/ok.mp3", "..", "ok.exe"):
            with self.assertRaises(InvalidInputError, msg=bad):
                video_maker.resolve_audio(bad)
        with self.assertRaises(NotFoundError):
            video_maker.resolve_audio("missing.mp3")
        self.assertEqual(video_maker.resolve_audio("ok.mp3"), (self.audio_root / "ok.mp3").resolve())

    def test_audio_root_unset(self):
        with mock.patch.object(settings, "AUDIO_ROOT", None):
            self.assertEqual(video_maker.list_audio_files(), [])
            with self.assertRaises(InvalidInputError):
                video_maker.resolve_audio("a.mp3")


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg غير مثبت")
class RenderTests(VideoMakerBase):
    def test_builds_vertical_h264_reel_from_mixed_images(self):
        make_image(self.folder / "1.jpg", (1600, 1000), "brown")
        make_image(self.folder / "2.png", (900, 1400), "navy")
        make_image(self.folder / "3.webp", (1000, 1000), "green")

        result = video_maker.create_product_video("B001")

        self.assertEqual(result.path, self.folder / "1.mp4")
        self.assertEqual(result.url, "https://example.com/B001/1.mp4")
        self.assertEqual(result.images_used, 3)
        self.assertFalse(result.replaced)
        self.assertLess(result.size_bytes, video_maker.MAX_BYTES)
        info = probe(result.path)
        stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        self.assertEqual((stream["codec_name"], stream["width"], stream["height"]), ("h264", 720, 1280))
        self.assertFalse(any(s["codec_type"] == "audio" for s in info["streams"]))
        self.assertAlmostEqual(float(info["format"]["duration"]), 7.0, delta=0.1)
        # لا ملفات مؤقتة متروكة بجانب الصور
        self.assertEqual([p.name for p in self.folder.glob(".*")], [])

    def test_crop_mode_and_single_image(self):
        make_image(self.folder / "1.jpg", (800, 800))
        result = video_maker.create_product_video("B001", fit="crop", duration=4)
        stream = next(s for s in probe(result.path)["streams"] if s["codec_type"] == "video")
        self.assertEqual((stream["width"], stream["height"]), (720, 1280))
        self.assertAlmostEqual(float(probe(result.path)["format"]["duration"]), 4.0, delta=0.1)

    def test_merges_short_audio_looped_to_video_length(self):
        make_image(self.folder / "1.jpg", (600, 600))
        # صوت أقصر (2 ثانية) من الفيديو (4): يجب أن يتكرر لا أن ينتهي مبكراً
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
             str(self.audio_root / "tone.wav")], check=True,
        )
        result = video_maker.create_product_video("B001", duration=4, audio="tone.wav")
        self.assertEqual(result.audio, "tone.wav")
        info = probe(result.path)
        kinds = {s["codec_type"]: s for s in info["streams"]}
        self.assertEqual(kinds["audio"]["codec_name"], "aac")
        self.assertAlmostEqual(float(info["format"]["duration"]), 4.0, delta=0.15)
        self.assertAlmostEqual(float(kinds["audio"]["duration"]), 4.0, delta=0.15)

    def test_existing_video_needs_overwrite_and_is_archived(self):
        make_image(self.folder / "1.jpg", (600, 600))
        video_maker.create_product_video("B001", duration=3)
        with self.assertRaises(InvalidInputError):
            video_maker.create_product_video("B001", duration=3)

        result = video_maker.create_product_video("B001", duration=3, overwrite=True)
        self.assertTrue(result.replaced)
        archived = list((self.backup / "B001").glob("1_*.mp4"))
        self.assertEqual(len(archived), 1)
        self.assertTrue((self.folder / "1.mp4").is_file())

    def test_ffmpeg_failure_leaves_no_partial_file(self):
        (self.folder / "1.jpg").write_bytes(b"not an image")
        with self.assertRaises(video_maker.VideoRenderError):
            video_maker.create_product_video("B001")
        self.assertFalse((self.folder / "1.mp4").exists())
        self.assertEqual([p.name for p in self.folder.glob(".*")], [])


if __name__ == "__main__":
    unittest.main()
