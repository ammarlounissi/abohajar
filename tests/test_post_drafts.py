"""النشر الجماعي المجدول: «توليد 30 مسودة» ثم المراجعة والموافقة، ولا رندرة ولا نشر قبل موافقة صاحب الصفحة."""
import hashlib
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from app import models
from app.core.config import settings
from app.models import PostStatus
from app.services import customer_captions, customer_workspaces, factory_video_jobs, ollama_chat, post_drafts
from app.services.errors import InvalidInputError
from tests.test_customer_workspace import BASE, WorkspaceTestBase

NOW = datetime(2026, 11, 12, 10, 0)            # UTC = 11:00 بتوقيت الجزائر
ALGIERS = ZoneInfo("Africa/Algiers")


def local(moment_iso: str) -> datetime:
    """الموعد كما تعيده الواجهة (UTC بلاحقة Z) بتوقيت الجزائر."""
    return datetime.fromisoformat(moment_iso.replace("Z", "+00:00")).astimezone(ALGIERS)


def iso(moment: datetime) -> str:
    return moment.replace(tzinfo=timezone.utc).isoformat()


class PostDraftTests(WorkspaceTestBase):
    def setUp(self):
        super().setUp()
        audio_root = Path(self.tmp.name) / "audio"
        audio_root.mkdir()
        (audio_root / "a.mp3").write_bytes(b"audio")
        (audio_root / "b.mp3").write_bytes(b"audio")
        # افتتاحيات مختلفة: ollama_chat يعيد كتابة الوصف إن تكررت أول كلمتين
        self.replies = iter(f"نص{n} فريد للمنشور" for n in range(1, 100))
        for patcher in (
            mock.patch.object(settings, "AUDIO_ROOT", audio_root),
            mock.patch.object(settings, "CUSTOMER_VIDEO_ENGINE", "ffmpeg"),
            mock.patch.object(settings, "PUBLISH_TIMEZONE", "Africa/Algiers"),
            mock.patch("app.services.video_maker.ffmpeg_tools.find_tool", return_value="/usr/bin/ffmpeg"),
            mock.patch("app.services.video_maker._run_ffmpeg", side_effect=self._write_video),
            mock.patch.object(post_drafts, "utc_now", side_effect=lambda: self.now),
            mock.patch.object(customer_workspaces, "utc_now", side_effect=lambda: self.now),
            mock.patch.object(customer_captions, "fetch_page_activity", return_value=""),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.now = NOW

    def _write_video(self, _ffmpeg, images, _duration, _fit, _crf, output, _audio, _template):
        output.write_bytes(b"generated-video")

    # ───── أدوات ─────

    def ready_client(self, user_id=1, page_id="111", images=3, activity="مخبزة: خبز تقليدي وحلويات"):
        self.link(user_id, page_id=page_id)
        client = self.client(user_id)
        for _ in range(images):
            self.assertEqual(self.upload(client).status_code, 201)
        if activity:
            self.assertEqual(client.patch(f"{BASE}/profile", json={"activity": activity}).status_code, 200)
        return client

    def write_captions(self, limit=100, error=None):
        """ما يفعله عامل الأوصاف ثم advance_all، حتى يفرغ الطابور. يرجع عدد المهام."""
        async def fake_reply(messages, model, provider=None):
            if error:
                raise error
            return next(self.replies)

        done = 0
        with mock.patch.object(ollama_chat, "generate_reply", side_effect=fake_reply):
            while done < limit:
                job_id = ollama_chat.claim_next_description_job(self.Session)
                if job_id is None:
                    break
                ollama_chat.process_description_job(job_id, self.Session)
                post_drafts.advance_all(self.Session)
                done += 1
        return done

    def render_all(self):
        """ما يفعله عامل الفيديو: كل مهمة في الطابور ثم advance_all."""
        post_drafts.advance_all(self.Session)
        while (job_id := factory_video_jobs.claim_next_video_job(self.Session)) is not None:
            factory_video_jobs.process_video_job(job_id, self.Session)
            post_drafts.advance_all(self.Session)

    def generate(self, client, count=3, at="20:00"):
        res = client.post(f"{BASE}/drafts", json={"count": count, "time": at})
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["drafts"]

    def drafts(self, client):
        res = client.get(f"{BASE}/drafts")
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["drafts"]

    def ready_drafts(self, client, count=3):
        self.generate(client, count)
        self.write_captions()
        drafts = self.drafts(client)
        self.assertTrue(all(d["status"] == "ready" for d in drafts))
        return drafts

    # ───── الوصول ─────

    def test_requires_login_and_a_linked_page(self):
        calls = (("get", "/drafts", None), ("post", "/drafts", {}), ("post", "/drafts/approve", {"ids": [1]}),
                 ("patch", "/drafts/1", {}), ("post", "/drafts/1/caption", None), ("delete", "/drafts/1", None))
        for method, path, body in calls:
            with self.subTest(path=path, method=method):
                kwargs = {"json": body} if body is not None else {}
                self.assertEqual(getattr(self.client(), method)(f"{BASE}{path}", **kwargs).status_code, 401)
                self.assertEqual(getattr(self.client(1), method)(f"{BASE}{path}", **kwargs).status_code, 409)

    def test_drafts_belong_to_the_users_own_page(self):
        first = self.ready_client(1, page_id="111")
        second = self.ready_client(2, page_id="222")
        draft = self.generate(first, 1)[0]
        self.assertEqual(self.drafts(second), [])
        self.assertEqual(second.patch(f"{BASE}/drafts/{draft['id']}", json={"caption": "x"}).status_code, 404)
        self.assertEqual(second.delete(f"{BASE}/drafts/{draft['id']}").status_code, 404)
        self.assertEqual(second.post(f"{BASE}/drafts/approve", json={"ids": [draft["id"]]}).status_code, 422)

    # ───── التوليد ─────

    def test_needs_activity_and_images(self):
        no_images = self.ready_client(1, page_id="111", images=0)
        self.assertEqual(no_images.post(f"{BASE}/drafts", json={"count": 3}).status_code, 422)
        no_activity = self.ready_client(2, page_id="222", activity=None)
        res = no_activity.post(f"{BASE}/drafts", json={"count": 3})
        self.assertEqual(res.status_code, 422)
        self.assertIn("نشاط", res.json()["detail"])

    def test_one_draft_a_day_from_tomorrow_at_the_chosen_local_time(self):
        client = self.ready_client()
        drafts = self.generate(client, 5, at="19:30")
        self.assertEqual(len(drafts), 5)
        days = [local(d["scheduled_at"]) for d in drafts]
        self.assertEqual([d.date().isoformat() for d in days],
                         ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16", "2026-11-17"])
        self.assertTrue(all((d.hour, d.minute) == (19, 30) for d in days))
        self.assertTrue(drafts[0]["scheduled_at"].endswith("Z") or drafts[0]["scheduled_at"].endswith("+00:00"))
        self.assertTrue(all(d["status"] == "writing" for d in drafts))
        # كل مسودة بصورها (من المكتبة، بلا تكرار) وقالبها وصوتها
        with self.Session() as db:
            library = {m.id for m in db.query(models.Media).filter_by(media_type="image")}
        for draft in drafts:
            ids = [image["id"] for image in draft["images"]]
            self.assertTrue(ids and set(ids) <= library and len(ids) == len(set(ids)))
            self.assertIn(draft["audio"], ("a.mp3", "b.mp3"))
            self.assertTrue(draft["template"])
        # ولا رندرة ولا منشور قبل الموافقة
        with self.Session() as db:
            self.assertEqual(db.query(models.FactoryVideoJob).count(), 0)
            self.assertEqual(db.query(models.Post).count(), 0)

    def test_new_batch_continues_after_the_days_already_taken(self):
        client = self.ready_client()
        self.generate(client, 2)
        more = self.generate(client, 2)
        self.assertEqual([local(d["scheduled_at"]).day for d in self.drafts(client)], [13, 14, 15, 16])
        self.assertEqual(len(more), 4)

    def test_captions_are_written_one_after_another(self):
        client = self.ready_client()
        self.generate(client, 3)
        with self.Session() as db:
            self.assertEqual(db.query(models.FactoryDescriptionJob).count(), 1)   # لا 3 دفعة واحدة
        self.assertEqual(self.write_captions(limit=1), 1)
        drafts = self.drafts(client)
        self.assertEqual([d["status"] for d in drafts], ["ready", "writing", "writing"])
        self.assertEqual(drafts[0]["caption"], "نص1 فريد للمنشور")
        self.write_captions()
        drafts = self.drafts(client)
        self.assertEqual([d["caption"] for d in drafts],
                         ["نص1 فريد للمنشور", "نص2 فريد للمنشور", "نص3 فريد للمنشور"])
        with self.Session() as db:
            jobs = db.query(models.FactoryDescriptionJob).order_by(models.FactoryDescriptionJob.id).all()
            self.assertEqual(len(jobs), 3)
            # الوصف الثاني كُتب وهو يرى الأول (قائمة التجنّب)
            self.assertIn("نص1", str(jobs[1].messages))

    def test_caption_carries_whatsapp_and_hashtags_from_settings(self):
        client = self.ready_client()
        client.patch(f"{BASE}/profile", json={"whatsapp": "0550123456", "hashtags": ["مخبزة"]})
        self.generate(client, 1)
        self.write_captions()
        caption = self.drafts(client)[0]["caption"]
        self.assertIn("نص1 فريد للمنشور", caption)
        self.assertIn("+213550123456", caption)
        self.assertTrue(caption.endswith("#مخبزة"))

    def test_failed_caption_leaves_an_empty_draft_to_write_by_hand(self):
        client = self.ready_client()
        self.generate(client, 2)
        self.write_captions(error=RuntimeError("quota"))
        drafts = self.drafts(client)
        self.assertEqual([d["status"] for d in drafts], ["ready", "ready"])
        self.assertEqual(drafts[0]["caption"], "")
        self.assertTrue(drafts[0]["caption_error"])

    def test_pending_review_limit(self):
        client = self.ready_client()
        self.generate(client, 30)
        res = client.post(f"{BASE}/drafts", json={"count": 1})
        self.assertEqual(res.status_code, 422)
        self.assertIn("بانتظار المراجعة", res.json()["detail"])
        self.assertEqual(client.post(f"{BASE}/drafts", json={"count": 31}).status_code, 422)

    # ───── المراجعة ─────

    def test_edit_caption_rewrite_and_delete_before_approval(self):
        client = self.ready_client()
        first, second, _ = self.ready_drafts(client)
        res = client.patch(f"{BASE}/drafts/{first['id']}", json={"caption": "  نص كتبته بنفسي  "})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["caption"], "نص كتبته بنفسي")
        res = client.post(f"{BASE}/drafts/{second['id']}/caption")
        self.assertEqual(res.json()["status"], "writing")
        self.write_captions()
        self.assertEqual(self.drafts(client)[1]["caption"], "نص4 فريد للمنشور")
        self.assertEqual(client.delete(f"{BASE}/drafts/{second['id']}").status_code, 204)
        self.assertEqual(len(self.drafts(client)), 2)

    def test_reschedule_respects_the_spam_rules(self):
        client = self.ready_client()
        first, second, third = self.ready_drafts(client)
        path = f"{BASE}/drafts/{third['id']}"
        soon = iso(NOW + timedelta(minutes=30))
        self.assertIn("ساعة", client.patch(path, json={"scheduled_at": soon}).json()["detail"])
        close = (local(first["scheduled_at"]) + timedelta(hours=1)).isoformat()
        self.assertIn("ساعات", client.patch(path, json={"scheduled_at": close}).json()["detail"])
        same_day = (local(first["scheduled_at"]) - timedelta(hours=5)).isoformat()
        self.assertEqual(client.patch(path, json={"scheduled_at": same_day}).status_code, 200)
        # يوم فيه منشوران: الثالث مرفوض
        third_on_day = (local(first["scheduled_at"]) - timedelta(hours=9)).isoformat()
        res = client.patch(f"{BASE}/drafts/{second['id']}", json={"scheduled_at": third_on_day})
        self.assertEqual(res.status_code, 422)
        self.assertIn("منشوران", res.json()["detail"])
        naive = client.patch(path, json={"scheduled_at": "2026-11-20T19:00:00"})
        self.assertEqual(naive.status_code, 422)

    # ───── الموافقة ─────

    def test_approve_records_who_when_and_which_version(self):
        client = self.ready_client()
        drafts = self.ready_drafts(client)
        client.patch(f"{BASE}/drafts/{drafts[0]['id']}", json={"caption": "النسخة التي وافقت عليها"})
        res = client.post(f"{BASE}/drafts/approve", json={"ids": [d["id"] for d in drafts]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(all(d["status"] == "approved" for d in res.json()["drafts"]))
        with self.Session() as db:
            rows = db.query(models.PostDraftApproval).order_by(models.PostDraftApproval.draft_id).all()
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0].user_id, 1)
            self.assertEqual(rows[0].page_id, "111")
            self.assertEqual(rows[0].approved_at, NOW)
            self.assertEqual(rows[0].caption, "النسخة التي وافقت عليها")
            self.assertEqual(rows[0].caption_sha256,
                             hashlib.sha256("النسخة التي وافقت عليها".encode()).hexdigest())
            self.assertEqual(rows[0].image_ids, [image["id"] for image in drafts[0]["images"]])
        # بعد الموافقة لا يتغير النص
        res = client.patch(f"{BASE}/drafts/{drafts[0]['id']}", json={"caption": "تعديل"})
        self.assertEqual(res.status_code, 422)

    def test_approve_refuses_drafts_still_writing_or_without_text(self):
        client = self.ready_client()
        drafts = self.generate(client, 2)
        res = client.post(f"{BASE}/drafts/approve", json={"ids": [d["id"] for d in drafts]})
        self.assertEqual(res.status_code, 422)
        self.write_captions()
        client.patch(f"{BASE}/drafts/{drafts[0]['id']}", json={"caption": "   "})
        res = client.post(f"{BASE}/drafts/approve", json={"ids": [d["id"] for d in drafts]})
        self.assertEqual(res.status_code, 422)
        self.assertIn("بلا نص", res.json()["detail"])
        with self.Session() as db:
            self.assertEqual(db.query(models.PostDraftApproval).count(), 0)    # لا موافقة جزئية

    def test_video_is_rendered_a_day_ahead_then_scheduled_for_the_publisher(self):
        client = self.ready_client()
        drafts = self.ready_drafts(client, 2)
        client.post(f"{BASE}/drafts/approve", json={"ids": [d["id"] for d in drafts]})
        self.render_all()
        self.assertEqual([d["status"] for d in self.drafts(client)], ["approved", "approved"])  # الموعد بعيد
        self.now = NOW + timedelta(hours=12)          # صار أول موعد (غداً 20:00) في حدود 24 ساعة
        self.render_all()
        first, second = self.drafts(client)
        self.assertEqual((first["status"], second["status"]), ("scheduled", "approved"))
        self.assertTrue(first["video_url"])
        with self.Session() as db:
            post = db.query(models.Post).one()
            self.assertEqual(post.status, PostStatus.APPROVED)
            self.assertEqual(post.description, drafts[0]["caption"])
            self.assertEqual(iso(post.scheduled_at).replace("+00:00", "Z"), first["scheduled_at"].replace("+00:00", "Z"))
            self.assertEqual(post.media_urls, [first["video_url"]])
            self.assertEqual(len(post.deliveries), 1)
            due = [p.id for p in db.query(models.Post).filter(models.Post.scheduled_at <= self.now)]
            self.assertEqual(due, [])                 # الناشر لا ينشره قبل موعده
        # مكتبة الفيديو تعرضه مجدولاً لا «قيد النشر»
        posts = client.get(f"{BASE}/videos/posts").json()
        self.assertEqual(posts[0]["status"], "scheduled")

    def test_publisher_result_shows_on_the_draft(self):
        client = self.ready_client()
        draft = self.ready_drafts(client, 1)[0]
        client.post(f"{BASE}/drafts/approve", json={"ids": [draft["id"]]})
        self.now = NOW + timedelta(hours=20)
        self.render_all()
        with self.Session() as db:
            post = db.query(models.Post).one()
            post.status, post.published_at = PostStatus.PUBLISHED, self.now
            post.deliveries[0].status, post.deliveries[0].remote_id = "published", "777"
            db.commit()
        view = self.drafts(client)[0]
        self.assertEqual(view["status"], "published")
        self.assertEqual(view["facebook_url"], "https://www.facebook.com/111/videos/777")
        self.assertEqual(client.delete(f"{BASE}/drafts/{draft['id']}").status_code, 422)

    def test_cancel_after_approval_stops_the_post(self):
        client = self.ready_client()
        draft = self.ready_drafts(client, 1)[0]
        client.post(f"{BASE}/drafts/approve", json={"ids": [draft["id"]]})
        self.now = NOW + timedelta(hours=20)
        self.render_all()
        self.assertEqual(client.delete(f"{BASE}/drafts/{draft['id']}").status_code, 204)
        self.assertEqual(self.drafts(client), [])
        with self.Session() as db:
            self.assertEqual(db.query(models.Post).one().status, PostStatus.ARCHIVED)

    def test_failed_render_is_retried_by_choosing_a_new_time(self):
        client = self.ready_client()
        draft = self.ready_drafts(client, 1)[0]
        client.post(f"{BASE}/drafts/approve", json={"ids": [draft["id"]]})
        self.now = NOW + timedelta(hours=20)
        with mock.patch("app.services.video_maker._run_ffmpeg", side_effect=RuntimeError("boom")):
            self.render_all()
        view = self.drafts(client)[0]
        self.assertEqual(view["status"], "failed")
        self.assertIn("تجهيز الفيديو", view["error"])
        new_time = (local(draft["scheduled_at"]) + timedelta(days=1)).isoformat()
        res = client.patch(f"{BASE}/drafts/{draft['id']}", json={"scheduled_at": new_time})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["status"], "approved")
        with self.Session() as db:
            self.assertEqual(db.query(models.PostDraftApproval).count(), 2)   # الموعد الجديد مسجّل

    def test_missed_slot_fails_instead_of_publishing_very_late(self):
        client = self.ready_client()
        draft = self.ready_drafts(client, 1)[0]
        client.post(f"{BASE}/drafts/approve", json={"ids": [draft["id"]]})
        self.now = NOW + timedelta(days=3)            # العامل كان متوقفاً
        view = self.drafts(client)[0]
        self.assertEqual(view["status"], "failed")
        self.assertIn("فات موعد", view["error"])

    def test_approval_needs_a_page_that_can_publish(self):
        client = self.ready_client()
        draft = self.ready_drafts(client, 1)[0]
        with self.Session() as db:
            db.query(models.SocialChannel).update({models.SocialChannel.page_access_token: None})
            db.commit()
        res = client.post(f"{BASE}/drafts/approve", json={"ids": [draft["id"]]})
        self.assertEqual(res.status_code, 422)
        self.assertIn("إعادة ربط", res.json()["detail"])


class SlotRuleTests(unittest.TestCase):
    """قيود المواعيد مباشرة (بلا HTTP)."""

    def test_plan_slots_skips_busy_days(self):
        with mock.patch.object(settings, "PUBLISH_TIMEZONE", "Africa/Algiers"):
            busy = [datetime(2026, 11, 14, 10, 0)]
            slots = post_drafts.plan_slots(3, time(20, 0), busy, now=NOW)
        self.assertEqual([s.day for s in slots], [13, 15, 16])
        self.assertTrue(all(s.hour == 19 for s in slots))       # 20:00 بتوقيت الجزائر = 19:00 UTC

    def test_check_slot(self):
        with mock.patch.object(settings, "PUBLISH_TIMEZONE", "Africa/Algiers"):
            post_drafts.check_slot(NOW + timedelta(days=1), [], now=NOW)
            for when, occupied in (
                (NOW + timedelta(minutes=10), []),
                (NOW + timedelta(days=200), []),
                (NOW + timedelta(days=1), [NOW + timedelta(days=1, hours=2)]),
                (NOW + timedelta(days=1, hours=6),
                 [NOW + timedelta(days=1), NOW + timedelta(days=1, hours=-6)]),
            ):
                with self.subTest(when=when), self.assertRaises(InvalidInputError):
                    post_drafts.check_slot(when, occupied, now=NOW)
