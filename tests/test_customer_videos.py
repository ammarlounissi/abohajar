"""فيديو الزبون من صور مكتبة صفحته: خيارات المعاينة، وطابور التوليد والمحرك اللذان تمر بهما منشوراته بعد الموافقة.

لا توليد يدوي للزبون: كل فيديو منشور يمر بالجدولة ثم التعديل ثم الموافقة ثم التوليد ثم النشر (tests/test_post_drafts.py).
هنا الطابور نفسه كما تطلبه مسودة معتمدة (enqueue)."""
import unittest
from pathlib import Path
from unittest import mock

from app import models
from app.core.config import settings
from app.services import customer_workspaces, errors, factory_video_jobs, media_library, video_maker
from tests.test_customer_workspace import BASE, WorkspaceTestBase


class CustomerVideoTests(WorkspaceTestBase):
    def setUp(self):
        super().setUp()
        audio_root = Path(self.tmp.name) / "audio"
        audio_root.mkdir()
        (audio_root / "a.mp3").write_bytes(b"audio")
        (audio_root / "z.wav").write_bytes(b"audio")
        self.rendered = []   # الصور التي وصلت إلى المحرك في كل رندرة، بترتيبها
        for patcher in (
            mock.patch.object(settings, "AUDIO_ROOT", audio_root),
            mock.patch.object(settings, "CUSTOMER_VIDEO_ENGINE", "ffmpeg"),
            mock.patch("app.services.video_maker.ffmpeg_tools.find_tool", return_value="/usr/bin/ffmpeg"),
            mock.patch("app.services.video_maker._run_ffmpeg", side_effect=self._write_video),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write_video(self, _ffmpeg, images, _duration, _fit, _crf, output, _audio, _template):
        self.rendered.append([image.name for image in images])
        output.write_bytes(b"generated-video")

    def run_worker_once(self):
        """ما يفعله عامل الفيديو لمهمة واحدة. يرجع رقم المهمة أو None إن كان الطابور فارغاً."""
        job_id = factory_video_jobs.claim_next_video_job(self.Session)
        if job_id is not None:
            factory_video_jobs.process_video_job(job_id, self.Session)
        return job_id

    def ready_client(self, user_id=1, page_id="111"):
        """زبون ربط صفحته ورفع صورة."""
        self.link(user_id, page_id=page_id)
        client = self.client(user_id)
        self.assertEqual(self.upload(client).status_code, 201)
        return client

    def enqueue(self, user_id=1, template="quick_7", audio="a.mp3", image_ids=None, engine="ffmpeg", page_id="111"):
        """ما تطلبه مسودة معتمدة من طابور الفيديو (post_drafts.advance_renders)."""
        with self.Session() as db:
            job = factory_video_jobs.enqueue_video_job(
                db, self.factory_of(user_id, page_id), source="library", sku=None, template=template, duration=None,
                audio=audio, image_ids=image_ids, engine=engine,
            )
            return job.id

    def job(self, job_id):
        with self.Session() as db:
            row = db.get(models.FactoryVideoJob, job_id)
            db.expunge(row)
            return row

    def images_of(self, client):
        """صور المكتبة: (المعرّف، اسم الملف) الأحدث أولاً."""
        return [(item["id"], item["url"].rsplit("/", 1)[-1]) for item in client.get(f"{BASE}/media?media_type=image").json()]

    # ───── الوصول ─────

    def test_requires_login_and_a_linked_page(self):
        for path in ("/videos/options", "/videos/audio/a.mp3", "/videos/archive"):
            with self.subTest(path=path):
                self.assertEqual(self.client().get(f"{BASE}{path}").status_code, 401)
                self.assertEqual(self.client(1).get(f"{BASE}{path}").status_code, 409)

    def test_no_manual_generation_or_instant_publishing(self):
        """التوليد والنشر يمران بالمنشورات وحدها (جدولة، تعديل، موافقة، توليد، نشر) فتُطبَّق قيود النشر."""
        client = self.ready_client()
        for method, path in (("post", "/videos"), ("get", "/videos/jobs"), ("get", "/videos/jobs/1"),
                             ("post", "/videos/1/publish"), ("get", "/videos/posts"), ("get", "/videos/library")):
            with self.subTest(path=path):
                kwargs = {"json": {}} if method == "post" else {}
                self.assertIn(getattr(client, method)(f"{BASE}{path}", **kwargs).status_code, (404, 405))
        with self.Session() as db:
            self.assertEqual(db.query(models.FactoryVideoJob).count(), 0)

    # ───── الخيارات ─────

    def test_options_list_templates_audio_and_counts(self):
        client = self.ready_client()
        body = client.get(f"{BASE}/videos/options").json()
        self.assertEqual((body["images_count"], body["videos_count"]), (1, 0))
        self.assertEqual(body["max_videos"], customer_workspaces.MAX_VIDEOS)
        self.assertEqual(body["audio_files"], ["a.mp3", "z.wav"])
        ids = [template["id"] for template in body["templates"]]
        self.assertIn(body["default_template"], ids)
        self.assertEqual(
            set(body["templates"][0]),
            {"id", "name", "duration", "max_images", "zoom", "pan", "transition", "crossfade", "card"},
        )
        for item in body["templates"]:
            spec = video_maker.template_spec(video_maker.get_video_template(item["id"]))
            self.assertEqual({key: item[key] for key in spec}, spec)
        self.assertNotIn("factory_id", body)
        self.assertNotIn("products", body)

    def test_audio_is_served_to_the_page_owner_for_preview(self):
        client = self.ready_client()
        res = client.get(f"{BASE}/videos/audio/z.wav")
        self.assertEqual((res.status_code, res.content), (200, b"audio"))
        self.assertIn("private", res.headers["cache-control"])
        self.assertEqual(client.get(f"{BASE}/videos/audio/missing.mp3").status_code, 404)
        self.assertEqual(client.get(f"{BASE}/videos/audio/notes.txt").status_code, 422)
        self.assertEqual(client.get(f"{BASE}/videos/audio/..%2Fa.mp3").status_code, 404)

    # ───── الطابور: ما تطلبه المسودة المعتمدة ─────

    def test_queued_video_is_generated_into_the_page_library(self):
        client = self.ready_client()
        job_id = self.enqueue(template="quick_7", audio="z.wav")
        self.assertEqual(self.run_worker_once(), job_id)
        done = self.job(job_id)
        self.assertEqual((done.status, done.images_used, done.error), ("succeeded", 1, None))
        factory_id = self.factory_of(1)
        self.assertTrue(done.video_url.startswith(f"http://testserver/api/media/factories/{factory_id}/"))
        saved = media_library.factory_media_file_path(factory_id, done.video_url.rsplit("/", 1)[-1])
        self.assertTrue(saved.is_file())
        videos = client.get(f"{BASE}/media?media_type=video").json()
        self.assertEqual([video["url"] for video in videos], [done.video_url])
        served = client.get(done.video_url.removeprefix("http://testserver"))
        self.assertEqual((served.status_code, served.headers["content-type"]), (200, "video/mp4"))

    def test_chosen_images_are_rendered_in_the_preview_order(self):
        client = self.ready_client()
        self.upload(client, "b.png")
        self.upload(client, "c.png")
        images = self.images_of(client)
        chosen = [images[2], images[0]]   # الأقدم ثم الأحدث، وتُترك الوسطى
        job_id = self.enqueue(image_ids=[i for i, _ in chosen])
        self.run_worker_once()
        self.assertEqual(self.rendered, [[name for _, name in chosen]])
        self.assertEqual((self.job(job_id).status, self.job(job_id).images_used), ("succeeded", 2))

    def test_images_deleted_after_the_request_are_skipped(self):
        client = self.ready_client()
        self.upload(client, "b.png")
        (first, _), (second, second_name) = self.images_of(client)
        self.enqueue(image_ids=[first, second])
        client.delete(f"{BASE}/media/{first}")
        self.run_worker_once()
        self.assertEqual(self.rendered, [[second_name]])

        failed_id = self.enqueue(image_ids=[second])
        client.delete(f"{BASE}/media/{second}")
        self.upload(client, "new.png")   # المكتبة ليست فارغة، لكن المختارة حُذفت كلها
        self.run_worker_once()
        failed = self.job(failed_id)
        self.assertEqual(failed.status, "failed")
        self.assertIn("حُذفت الصور المختارة", failed.error)

    def test_failed_generation_is_reported_on_the_job(self):
        self.ready_client()
        job_id = self.enqueue()
        with mock.patch(
            "app.services.factory_video_jobs.factory_videos.generate_factory_video",
            side_effect=errors.InvalidInputError("تعذّر بناء الفيديو"),
        ):
            self.run_worker_once()
        failed = self.job(job_id)
        self.assertEqual((failed.status, failed.error, failed.video_url), ("failed", "تعذّر بناء الفيديو", None))

    def test_generated_video_can_be_deleted_from_the_library(self):
        client = self.ready_client()
        self.enqueue()
        self.run_worker_once()
        video = client.get(f"{BASE}/media?media_type=video").json()[0]
        saved = media_library.factory_media_file_path(self.factory_of(1), video["url"].rsplit("/", 1)[-1])
        self.assertEqual(client.delete(f"{BASE}/media/{video['id']}").status_code, 204)
        self.assertFalse(saved.exists())
        self.assertEqual(client.get(f"{BASE}/videos/options").json()["videos_count"], 0)

    # ───── المحرك ─────

    def test_remotion_engine_renders_the_job(self):
        self.ready_client()
        job_id = self.enqueue(template="cards_15", audio="a.mp3", engine="remotion")

        def remotion(images, output, *, duration, audio, max_bytes, template, fit="blur"):
            self.assertEqual((duration, template.id, audio.name), (15, "cards_15", "a.mp3"))
            output.write_bytes(b"remotion-video")
            return output.stat().st_size

        with mock.patch("app.services.factory_videos.remotion_render.render_video_file", side_effect=remotion) as render:
            self.run_worker_once()
        render.assert_called_once()
        self.assertEqual(self.rendered, [])   # ffmpeg لم يُستدعَ
        self.assertEqual(self.job(job_id).status, "succeeded")

    def test_ffmpeg_engine_keeps_ffmpeg(self):
        self.ready_client()
        self.enqueue(engine="ffmpeg")
        self.run_worker_once()
        self.assertEqual(len(self.rendered), 1)

    # ───── العزل ─────

    def test_videos_are_scoped_to_the_users_page(self):
        mine = self.ready_client(1, "111")
        other = self.ready_client(2, "222")
        self.enqueue(user_id=1)
        self.enqueue(user_id=2, page_id="222")
        self.run_worker_once()
        self.run_worker_once()
        self.assertEqual(len(mine.get(f"{BASE}/media?media_type=video").json()), 1)
        self.assertEqual(len(other.get(f"{BASE}/media?media_type=video").json()), 1)
        self.assertNotEqual(
            mine.get(f"{BASE}/media?media_type=video").json()[0]["url"],
            other.get(f"{BASE}/media?media_type=video").json()[0]["url"],
        )


if __name__ == "__main__":
    unittest.main()
