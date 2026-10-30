"""ريل كامل بمعرّف المصنع: الطلب يُنشئ مهمتين ثم يُنهى منشوراً معتمداً عند نجاحهما (أو يفشل بسبب واضح).
قاعدة SQLite في الذاكرة، ومهمة الفيديو (تحتاج صوراً وffmpeg) مُستبدلة بإنشاء سجل المهمة مباشرة."""
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.database import Base
from app.services import errors, factory_reel_requests as reels


def fake_enqueue_video(db, factory_id):
    job = models.FactoryVideoJob(
        factory_id=factory_id, source="library", template="cinematic_30", duration=30, status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


class ReelRequestCase(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add(models.Factory(id=1, name="مصنع الأحجار", phone_number="213550000000", activity="أحجار ديكور"))
            db.add(models.Factory(id=2, name="بلا نشاط", phone_number="213550000001", activity=""))
            db.add(models.SocialChannel(id=1, factory_id=1, platform="facebook", name="ص", remote_id="1"))
            db.add(models.SocialChannel(id=2, factory_id=2, platform="facebook", name="ص2", remote_id="2"))
            db.commit()
        patcher = mock.patch.object(reels.factory_video_jobs, "enqueue_automated_library_video", fake_enqueue_video)
        patcher.start()
        self.addCleanup(patcher.stop)

    def enqueue(self, factory_id=1):
        with self.Session() as db:
            request = reels.enqueue_reel_request(db, factory_id)
            return request.id, request.video_job_id, request.chat_job_id

    def finish(self, video_id, chat_id, video="succeeded", chat="succeeded"):
        with self.Session() as db:
            v = db.get(models.FactoryVideoJob, video_id)
            v.status, v.video_url, v.error = video, "https://x.example/a.mp4" if video == "succeeded" else None, \
                "ffmpeg" if video == "failed" else None
            c = db.get(models.FactoryDescriptionJob, chat_id)
            c.status, c.error = chat, "ollama" if chat == "failed" else None
            c.response = '```json\n{"description": "**وصف** جاهز"}\n```' if chat == "succeeded" else None
            db.commit()


class EnqueueTests(ReelRequestCase):
    def test_creates_both_jobs_and_pending_request(self):
        request_id, video_id, chat_id = self.enqueue()
        with self.Session() as db:
            request = db.get(models.FactoryReelRequest, request_id)
            self.assertEqual((request.status, request.post_id), ("pending", None))
            self.assertEqual((request.video_status, request.chat_status), ("queued", "queued"))
            self.assertEqual(db.get(models.FactoryDescriptionJob, chat_id).factory_id, 1)

    def test_factory_without_activity_creates_no_jobs(self):
        with self.Session() as db:
            with self.assertRaises(errors.InvalidInputError):
                reels.enqueue_reel_request(db, 2)
            self.assertEqual(db.query(models.FactoryVideoJob).count(), 0)
            self.assertEqual(db.query(models.FactoryDescriptionJob).count(), 0)

    def test_factory_without_active_channels_creates_no_jobs(self):
        with self.Session() as db:
            db.query(models.SocialChannel).update({"is_active": False})
            db.commit()
            with self.assertRaises(errors.InvalidInputError):
                reels.enqueue_reel_request(db, 1)
            self.assertEqual(db.query(models.FactoryVideoJob).count(), 0)

    def test_unknown_factory(self):
        with self.Session() as db:
            with self.assertRaises(errors.NotFoundError):
                reels.enqueue_reel_request(db, 99)


class FinalizeTests(ReelRequestCase):
    def test_stays_pending_until_both_succeed(self):
        request_id, video_id, chat_id = self.enqueue()
        self.finish(video_id, chat_id, chat="running")
        self.assertEqual(reels.finalize_pending(self.Session), 0)
        with self.Session() as db:
            self.assertEqual(db.get(models.FactoryReelRequest, request_id).status, "pending")
            self.assertEqual(db.query(models.Post).count(), 0)

    def test_creates_approved_post_once(self):
        request_id, video_id, chat_id = self.enqueue()
        self.finish(video_id, chat_id)
        self.assertEqual(reels.finalize_pending(self.Session), 1)
        self.assertEqual(reels.finalize_pending(self.Session), 0)      # تكرار الاستدعاء لا ينشئ منشوراً ثانياً
        with self.Session() as db:
            request = db.get(models.FactoryReelRequest, request_id)
            self.assertEqual(request.status, "succeeded")
            posts = db.query(models.Post).all()
            self.assertEqual(len(posts), 1)
            post = posts[0]
            self.assertEqual(request.post_id, post.id)
            self.assertEqual((post.media_type, post.status.value, post.approval.value), ("reel", "approved", "approved"))
            self.assertEqual(post.media_urls, ["https://x.example/a.mp4"])
            self.assertTrue(post.description.startswith("وصف جاهز\n"))
            self.assertEqual([d.channel_id for d in post.deliveries], [1])

    def test_failed_job_closes_request_with_reason(self):
        request_id, video_id, chat_id = self.enqueue()
        self.finish(video_id, chat_id, video="failed")
        reels.finalize_pending(self.Session)
        with self.Session() as db:
            request = db.get(models.FactoryReelRequest, request_id)
            self.assertEqual(request.status, "failed")
            self.assertIn("فشل الفيديو: ffmpeg", request.error)
            self.assertEqual(db.query(models.Post).count(), 0)

    def test_failed_description_falls_back_to_previous_ollama_message(self):
        request_id, video_id, chat_id = self.enqueue()
        with self.Session() as db:
            db.add(models.FactoryChatMessage(factory_id=1, prompt="p", response="وصف قديم للمصنع"))
            db.commit()
        self.finish(video_id, chat_id, chat="failed")
        reels.finalize_pending(self.Session)
        with self.Session() as db:
            request = db.get(models.FactoryReelRequest, request_id)
            self.assertEqual(request.status, "succeeded")
            self.assertTrue(db.get(models.Post, request.post_id).description.startswith("وصف قديم للمصنع\n"))

    def test_failed_description_without_previous_message_still_fails(self):
        request_id, video_id, chat_id = self.enqueue()
        self.finish(video_id, chat_id, chat="failed")
        reels.finalize_pending(self.Session)
        with self.Session() as db:
            request = db.get(models.FactoryReelRequest, request_id)
            self.assertEqual(request.status, "failed")
            self.assertIn("فشل الوصف: ollama", request.error)

    def test_failed_description_waits_for_video_when_fallback_exists(self):
        request_id, video_id, chat_id = self.enqueue()
        with self.Session() as db:
            db.add(models.FactoryChatMessage(factory_id=1, prompt="p", response="وصف قديم"))
            db.commit()
        self.finish(video_id, chat_id, video="queued", chat="failed")
        reels.finalize_pending(self.Session)
        with self.Session() as db:
            self.assertEqual(db.get(models.FactoryReelRequest, request_id).status, "pending")

    def test_failed_video_falls_back_to_random_library_video(self):
        request_id, video_id, chat_id = self.enqueue()
        with self.Session() as db:
            db.add(models.Media(factory_id=1, media_type="video", url="https://x.example/lib.mp4"))
            db.add(models.Media(factory_id=1, media_type="image", url="https://x.example/a.jpg"))
            db.commit()
        self.finish(video_id, chat_id, video="failed")
        reels.finalize_pending(self.Session)
        with self.Session() as db:
            request = db.get(models.FactoryReelRequest, request_id)
            self.assertEqual(request.status, "succeeded")
            self.assertEqual(db.get(models.Post, request.post_id).media_urls, ["https://x.example/lib.mp4"])

    def test_failed_video_without_library_video_still_fails(self):
        request_id, video_id, chat_id = self.enqueue()
        self.finish(video_id, chat_id, video="failed")
        reels.finalize_pending(self.Session)
        with self.Session() as db:
            request = db.get(models.FactoryReelRequest, request_id)
            self.assertEqual(request.status, "failed")
            self.assertIn("فشل الفيديو: ffmpeg", request.error)

    def test_post_creation_error_closes_request_failed(self):
        request_id, video_id, chat_id = self.enqueue()
        self.finish(video_id, chat_id)
        with self.Session() as db:
            db.query(models.SocialChannel).update({"is_active": False})
            db.commit()
        reels.finalize_pending(self.Session)
        with self.Session() as db:
            request = db.get(models.FactoryReelRequest, request_id)
            self.assertEqual(request.status, "failed")
            self.assertIn("قنوات", request.error)

    def test_get_request_is_scoped_to_factory(self):
        request_id, _, _ = self.enqueue()
        with self.Session() as db:
            self.assertEqual(reels.get_reel_request(db, 1, request_id).id, request_id)
            with self.assertRaises(errors.NotFoundError):
                reels.get_reel_request(db, 2, request_id)


if __name__ == "__main__":
    unittest.main()
