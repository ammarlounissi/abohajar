"""رندرة Remotion من جهة الخلفية: ما يُرسل إلى سكريبت الواجهة (remotion/render.mjs)، وإعادة الضغط، والأخطاء.
السكريبت نفسه لا يُشغَّل هنا (يتطلب Node وChrome)؛ نقلّد subprocess.run."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.core.config import settings
from app.services import remotion_render, video_maker


class RemotionRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.project = root / "ikhlasdz"
        (self.project / "remotion").mkdir(parents=True)
        (self.project / "remotion" / "render.mjs").write_text("// stub")
        self.images = []
        for name in ("one.JPG", "two.png"):
            path = root / name
            path.write_bytes(name.encode())
            self.images.append(path)
        self.audio = root / "song.mp3"
        self.audio.write_bytes(b"song")
        self.output = root / "out.mp4"
        self.jobs = []
        for patcher in (
            mock.patch.object(settings, "REMOTION_DIR", self.project),
            mock.patch.object(settings, "REMOTION_CONCURRENCY", 3),
            mock.patch("app.services.remotion_render.ffmpeg_tools.find_tool", return_value=None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_node(self, sizes):
        """يقلّد node render.mjs: يحفظ الطلب ويتحقق من الأصول، ويكتب MP4 بالحجم التالي من sizes."""
        sizes = list(sizes)

        def run(command, *, cwd, input, **kwargs):
            job = json.loads(input)
            assets = Path(job["assets_dir"])
            job["assets"] = {path.name: path.read_bytes() for path in assets.iterdir()}
            self.jobs.append(job)
            self.assertEqual((command[1], Path(cwd)), (str(remotion_render.RENDER_SCRIPT), self.project))
            Path(job["output"]).write_bytes(b"x" * sizes.pop(0))
            return mock.Mock(returncode=0, stdout="", stderr="")

        return mock.patch("app.services.remotion_render.subprocess.run", side_effect=run)

    def render(self, max_bytes=1000, template="showcase_30", audio=True):
        return remotion_render.render_video_file(
            self.images, self.output, duration=30, audio=self.audio if audio else None,
            max_bytes=max_bytes, template=video_maker.get_video_template(template),
        )

    def test_sends_template_spec_and_local_assets_in_order(self):
        with self.fake_node([100]):
            self.assertEqual(self.render(), 100)
        self.assertEqual(self.output.read_bytes(), b"x" * 100)
        job = self.jobs[0]
        self.assertEqual((job["composition"], job["crf"], job["concurrency"]), ("ProductReel", 23, 3))
        self.assertEqual(job["props"], {
            "images": ["0.jpg", "1.png"], "audio": "audio.mp3", "duration": 30,
            "zoom": 0.12, "pan": True, "transition": "circleopen", "crossfade": 0.35, "card": False,
        })
        self.assertEqual(job["assets"], {"0.jpg": b"one.JPG", "1.png": b"two.png", "audio.mp3": b"song"})
        self.assertFalse(Path(job["work_dir"]).exists())   # المجلد المؤقت يُحذف بعد الرندرة

    def test_without_audio(self):
        with self.fake_node([10]):
            self.render(audio=False)
        self.assertIsNone(self.jobs[0]["props"]["audio"])

    def test_rerenders_smaller_then_fails_if_still_too_big(self):
        with self.fake_node([2000, 500]):
            self.assertEqual(self.render(), 500)
        self.assertEqual([job["crf"] for job in self.jobs], [23, 28])
        self.jobs.clear()
        with self.fake_node([2000, 1500]):
            with self.assertRaises(video_maker.VideoRenderError):
                self.render()

    def test_missing_project_or_node_or_failure_are_clear_errors(self):
        with mock.patch.object(settings, "REMOTION_DIR", None):
            with self.assertRaisesRegex(video_maker.VideoToolMissingError, "REMOTION_DIR"):
                self.render()
        with mock.patch("app.services.remotion_render.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(video_maker.VideoToolMissingError, "Node"):
                self.render()
        failed = mock.Mock(returncode=1, stdout="", stderr="Error: chrome crashed")
        with mock.patch("app.services.remotion_render.subprocess.run", return_value=failed):
            with self.assertRaises(video_maker.VideoRenderError):
                self.render()


if __name__ == "__main__":
    unittest.main()
