"""توليد الفيديو للزبون من صور مكتبة صفحته: الطابور والمحرك الموجودان، بمسارات تعرف الصفحة من الجلسة."""
import unittest
from pathlib import Path
from unittest import mock

from app import models
from app.core.config import settings
from app.services import customer_workspaces, errors, factory_video_jobs, media_library
from tests.test_customer_workspace import BASE, WorkspaceTestBase


class CustomerVideoTests(WorkspaceTestBase):
    def setUp(self):
        super().setUp()
        audio_root = Path(self.tmp.name) / "audio"
        audio_root.mkdir()
        (audio_root / "a.mp3").write_bytes(b"audio")
        (audio_root / "z.wav").write_bytes(b"audio")
        for patcher in (
            mock.patch.object(settings, "AUDIO_ROOT", audio_root),
            mock.patch("app.services.video_maker.ffmpeg_tools.find_tool", return_value="/usr/bin/ffmpeg"),
            mock.patch("app.services.video_maker._run_ffmpeg", side_effect=self._write_video),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def _write_video(_ffmpeg, _images, _duration, _fit, _crf, output, _audio, _template):
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

    # ───── الوصول ─────

    def test_requires_login_and_a_linked_page(self):
        for method, path in (("get", "/videos/options"), ("post", "/videos"), ("get", "/videos/jobs"),
                             ("get", "/videos/jobs/1")):
            with self.subTest(path=path):
                kwargs = {"json": {}} if method == "post" else {}
                self.assertEqual(getattr(self.client(), method)(f"{BASE}{path}", **kwargs).status_code, 401)
                self.assertEqual(getattr(self.client(1), method)(f"{BASE}{path}", **kwargs).status_code, 409)

    # ───── الخيارات ─────

    def test_options_list_templates_audio_and_counts(self):
        client = self.ready_client()
        body = client.get(f"{BASE}/videos/options").json()
        self.assertEqual((body["images_count"], body["videos_count"]), (1, 0))
        self.assertEqual(body["max_videos"], customer_workspaces.MAX_VIDEOS)
        self.assertEqual(body["audio_files"], ["a.mp3", "z.wav"])
        ids = [template["id"] for template in body["templates"]]
        self.assertIn(body["default_template"], ids)
        self.assertEqual(set(body["templates"][0]), {"id", "name", "duration", "max_images"})
        self.assertNotIn("factory_id", body)
        self.assertNotIn("products", body)

    # ───── الطلب والتوليد ─────

    def test_request_is_queued_then_generated_into_the_page_library(self):
        client = self.ready_client()
        res = client.post(f"{BASE}/videos", json={"template": "quick_7", "audio": "z.wav"})
        self.assertEqual(res.status_code, 202, res.text)
        job = res.json()
        self.assertEqual((job["status"], job["template"], job["duration"], job["audio"]), ("queued", "quick_7", 7, "z.wav"))
        self.assertIsNone(job["video_url"])
        self.assertNotIn("factory_id", job)

        self.assertEqual(self.run_worker_once(), job["id"])
        done = client.get(f"{BASE}/videos/jobs/{job['id']}").json()
        self.assertEqual((done["status"], done["images_used"], done["error"]), ("succeeded", 1, None))

        factory_id = self.factory_of(1)
        self.assertTrue(done["video_url"].startswith(f"http://testserver/api/media/factories/{factory_id}/"))
        saved = media_library.factory_media_file_path(factory_id, done["video_url"].rsplit("/", 1)[-1])
        self.assertTrue(saved.is_file())
        videos = client.get(f"{BASE}/media?media_type=video").json()
        self.assertEqual([video["url"] for video in videos], [done["video_url"]])
        self.assertEqual(client.get(f"{BASE}/workspace").json()["videos_count"], 1)
        served = client.get(done["video_url"].removeprefix("http://testserver"))
        self.assertEqual((served.status_code, served.headers["content-type"]), (200, "video/mp4"))

        self.assertEqual([j["id"] for j in client.get(f"{BASE}/videos/jobs").json()], [job["id"]])
        with self.Session() as db:
            row = db.get(models.FactoryVideoJob, job["id"])
            self.assertEqual((row.factory_id, row.source, row.sku), (factory_id, "library", None))

    def test_defaults_use_the_default_template_and_a_random_available_audio(self):
        client = self.ready_client()
        job = client.post(f"{BASE}/videos", json={}).json()
        self.assertEqual(job["template"], client.get(f"{BASE}/videos/options").json()["default_template"])
        self.assertIn(job["audio"], ["a.mp3", "z.wav"])

    def test_generated_video_can_be_deleted_from_the_library(self):
        client = self.ready_client()
        client.post(f"{BASE}/videos", json={})
        self.run_worker_once()
        video = client.get(f"{BASE}/media?media_type=video").json()[0]
        saved = media_library.factory_media_file_path(self.factory_of(1), video["url"].rsplit("/", 1)[-1])
        self.assertEqual(client.delete(f"{BASE}/media/{video['id']}").status_code, 204)
        self.assertFalse(saved.exists())
        self.assertEqual(client.get(f"{BASE}/videos/options").json()["videos_count"], 0)

    def test_failed_generation_is_reported_on_the_job(self):
        client = self.ready_client()
        job = client.post(f"{BASE}/videos", json={}).json()
        with mock.patch(
            "app.services.factory_video_jobs.factory_videos.generate_factory_video",
            side_effect=errors.InvalidInputError("تعذّر بناء الفيديو"),
        ):
            self.run_worker_once()
        failed = client.get(f"{BASE}/videos/jobs/{job['id']}").json()
        self.assertEqual((failed["status"], failed["error"], failed["video_url"]), ("failed", "تعذّر بناء الفيديو", None))
        self.assertEqual(client.post(f"{BASE}/videos", json={}).status_code, 202)   # الفاشلة لا تحجز الدور

    # ───── الرفض ─────

    def test_rejects_requests_that_cannot_be_generated(self):
        self.link(1)
        client = self.client(1)
        res = client.post(f"{BASE}/videos", json={})
        self.assertEqual(res.status_code, 422)
        self.assertIn("ارفع صوراً", res.json()["detail"])
        self.upload(client)
        self.assertEqual(client.post(f"{BASE}/videos", json={"template": "nope"}).status_code, 422)
        self.assertEqual(client.post(f"{BASE}/videos", json={"audio": "missing.mp3"}).status_code, 404)
        self.assertEqual(client.post(f"{BASE}/videos", json={"audio": "../a.mp3"}).status_code, 422)
        with self.Session() as db:
            self.assertEqual(db.query(models.FactoryVideoJob).count(), 0)

    def test_one_running_job_per_page(self):
        client = self.ready_client()
        self.assertEqual(client.post(f"{BASE}/videos", json={}).status_code, 202)
        res = client.post(f"{BASE}/videos", json={})
        self.assertEqual(res.status_code, 422)
        self.assertIn("قيد التوليد", res.json()["detail"])
        self.run_worker_once()
        self.assertEqual(client.post(f"{BASE}/videos", json={}).status_code, 202)

    def test_video_quota(self):
        client = self.ready_client()
        with mock.patch.object(customer_workspaces, "MAX_VIDEOS", 1):
            client.post(f"{BASE}/videos", json={})
            self.run_worker_once()
            res = client.post(f"{BASE}/videos", json={})
            self.assertEqual(res.status_code, 422)
            self.assertIn("الحد الأقصى", res.json()["detail"])

    # ───── العزل ─────

    def test_jobs_and_videos_are_scoped_to_the_users_page(self):
        mine = self.ready_client(1, "111")
        other = self.ready_client(2, "222")
        job = mine.post(f"{BASE}/videos", json={}).json()
        self.assertEqual(other.get(f"{BASE}/videos/jobs/{job['id']}").status_code, 404)
        self.assertEqual(other.get(f"{BASE}/videos/jobs").json(), [])
        self.assertEqual(other.post(f"{BASE}/videos", json={}).status_code, 202)   # مهمة غيره لا تحجز دوره
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
