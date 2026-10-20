"""منطق النشر: الحجز الذري، انتقال الحالات، الفشل الجزئي وإعادة المحاولة، التقارير، العالقون.
قاعدة SQLite في الذاكرة ومحوّلات وهمية، بلا شبكة."""
import asyncio
import unittest
from datetime import datetime, timedelta
from unittest import mock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.config import settings
from app.core.database import Base
from app.models import PostStatus, ReportKind
from app.services import post_publisher
from app.services.errors import ConflictError, InvalidInputError
from app.services.social.base import Publisher, PublishError

NOW = datetime(2026, 10, 17, 7, 0, 0)


def run(coro):
    return asyncio.run(coro)


class FakePublisher(Publisher):
    def __init__(self, platform, fail=None, crash=False, invalid=None):
        self.platform = platform
        self.fail = fail          # PublishError لإفشال النشر
        self.crash = crash
        self.invalid = invalid
        self._counter = {"n": 0}      # مشترك مع نسخ for_channel (shallow copy) فيرى الاختبار كل الاستدعاءات

    @property
    def calls(self):
        return self._counter["n"]

    def validate(self, post):
        return self.invalid

    async def _publish(self, post):
        self._counter["n"] += 1
        if self.crash:
            raise RuntimeError("boom")
        if self.fail:
            raise self.fail
        return f"{self.platform}-remote-{post.id}"


class Base_(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(self.db.close)
        self.db.add(models.Factory(id=1, name="مصنع", phone_number="213550000000"))
        # قناة لكل منصة، ومعرّفها الداخلي في self.ch (tiktok بلا محوّل لاختبار المنصة غير المدعومة)
        self.ch = {}
        for i, (platform, remote) in enumerate((("facebook", "111111"), ("instagram", "222222"), ("tiktok", "333333")), 1):
            self.db.add(models.SocialChannel(id=i, platform=platform, name=platform, remote_id=remote))
            self.ch[platform] = i
        self.db.commit()
        for name, value in (("PUBLISH_DAILY_LIMIT", 50), ("PUBLISH_STALE_MINUTES", 30)):
            p = mock.patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)

    def make_post(self, status=PostStatus.APPROVED, platforms="facebook,instagram", scheduled_at=None,
                  approved_at=NOW - timedelta(hours=1), media_type="image", **kw):
        post = models.Post(
            factory_id=1, media_type=media_type, media_urls=[] if media_type == "text" else ["https://x/1.jpg"],
            description="نص", status=status, scheduled_at=scheduled_at,
            approved_at=approved_at, **kw,
        )
        self.db.add(post)
        self.db.commit()
        # platforms: أسماء المنصات مفصولة بفواصل (اختصار للاختبار)، وتصير وجهة pending لقناة كل منصة
        for name in (platforms.split(",") if platforms else []):
            self.db.add(models.PostDelivery(post_id=post.id, channel_id=self.ch[name], status="pending", attempts=0))
        self.db.commit()
        return post

    def go(self, publishers, **kw):
        return run(post_publisher.publish_due(self.db, publishers=publishers, now=NOW, **kw))


class ClaimTests(Base_):
    def test_claim_is_exclusive(self):
        post = self.make_post()
        first = post_publisher.claim_next(self.db, NOW)
        second = post_publisher.claim_next(self.db, NOW)
        self.assertEqual(first.id, post.id)
        self.assertEqual(first.status, PostStatus.PUBLISHING)
        self.assertEqual(first.claimed_at, NOW)
        self.assertIsNone(second)

    def test_future_scheduled_not_claimed(self):
        self.make_post(scheduled_at=NOW + timedelta(hours=1))
        self.assertIsNone(post_publisher.claim_next(self.db, NOW))

    def test_only_approved_are_claimed(self):
        for status in (PostStatus.DRAFT, PostStatus.SCHEDULED, PostStatus.PUBLISHED, PostStatus.FAILED, PostStatus.ARCHIVED):
            self.make_post(status=status)
        self.assertIsNone(post_publisher.claim_next(self.db, NOW))

    def test_oldest_due_first(self):
        late = self.make_post(approved_at=NOW - timedelta(hours=1))
        early = self.make_post(approved_at=NOW - timedelta(hours=5))
        self.assertEqual(post_publisher.claim_next(self.db, NOW).id, early.id)
        self.assertEqual(post_publisher.claim_next(self.db, NOW).id, late.id)


class PublishFlowTests(Base_):
    def test_all_platforms_ok(self):
        post = self.make_post()
        summary = self.go({"facebook": FakePublisher("facebook"), "instagram": FakePublisher("instagram")})
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.PUBLISHED)
        self.assertIsNotNone(post.published_at)
        self.assertEqual({d.platform: d.status for d in post.deliveries}, {"facebook": "published", "instagram": "published"})
        self.assertTrue(all(d.remote_id for d in post.deliveries))
        self.assertEqual(summary.failures, 0)
        self.assertEqual(self.db.scalars(select(models.PublishReport)).all(), [])

    def test_one_post_per_run(self):
        self.make_post()
        self.make_post()
        summary = self.go({"facebook": FakePublisher("facebook"), "instagram": FakePublisher("instagram")})
        self.assertEqual(len(summary.outcomes), 1)
        left = self.db.scalars(select(models.Post).where(models.Post.status == PostStatus.APPROVED)).all()
        self.assertEqual(len(left), 1)

    def test_partial_failure_then_retry_only_failed_platform(self):
        post = self.make_post()
        fb = FakePublisher("facebook")
        ig = FakePublisher("instagram", fail=PublishError(ReportKind.MEDIA, "(#9004) media download failed"))
        self.go({"facebook": fb, "instagram": ig})
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.FAILED)
        self.assertIn("instagram", post.error)
        states = {d.platform: d.status for d in post.deliveries}
        self.assertEqual(states, {"facebook": "published", "instagram": "failed"})
        report = self.db.scalars(select(models.PublishReport)).one()
        self.assertEqual((report.post_id, report.platform, report.kind), (post.id, "instagram", "media"))
        self.assertIsNone(report.sent_at)

        # إعادة المحاولة: اعتماد جديد ثم تشغيل، فتُنشر إنستغرام وحدها
        post_publisher.approve_post(self.db, post.id, now=NOW)
        ig_ok = FakePublisher("instagram")
        summary = self.go({"facebook": fb, "instagram": ig_ok})
        self.db.refresh(post)
        self.assertEqual(fb.calls, 1)                       # لم يُنشر على فيسبوك مرة ثانية
        self.assertEqual(ig_ok.calls, 1)
        self.assertEqual(post.status, PostStatus.PUBLISHED)
        self.assertIsNone(post.error)
        self.assertEqual(summary.outcomes[0].skipped, ["facebook:111111"])
        self.assertEqual({d.platform: d.attempts for d in post.deliveries}, {"facebook": 1, "instagram": 2})

    def test_all_fail_marks_failed_with_token_kind(self):
        post = self.make_post()
        err = PublishError(ReportKind.TOKEN, "(#190) invalid token")
        self.go({"facebook": FakePublisher("facebook", fail=err), "instagram": FakePublisher("instagram", fail=err)})
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.FAILED)
        kinds = [r.kind for r in self.db.scalars(select(models.PublishReport))]
        self.assertEqual(kinds, ["token", "token"])

    def test_validation_failure_is_reported_without_calling_meta(self):
        post = self.make_post(platforms="instagram", media_type="text")
        ig = FakePublisher("instagram", invalid=PublishError(ReportKind.MEDIA, "instagram_requires_media"))
        self.go({"instagram": ig})
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.FAILED)
        self.assertEqual(ig.calls, 0)
        self.assertEqual(self.db.scalars(select(models.PublishReport)).one().message, "[222222] instagram_requires_media")

    def test_unsupported_platform_and_crash_do_not_leave_post_stuck(self):
        post = self.make_post(platforms="tiktok,facebook")
        self.go({"facebook": FakePublisher("facebook", crash=True)})
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.FAILED)
        messages = sorted(r.message for r in self.db.scalars(select(models.PublishReport)))
        self.assertEqual(len(messages), 2)
        self.assertTrue(messages[0].startswith("[111111] RuntimeError"))      # facebook
        self.assertEqual(messages[1], "[333333] unsupported_platform:tiktok")

    def test_post_without_channels_fails_cleanly(self):
        post = self.make_post(platforms=None)
        self.go({})
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.FAILED)
        self.assertEqual(post.error, "no_channels")


class LimitsAndDryRunTests(Base_):
    def test_daily_limit(self):
        self.make_post()
        with mock.patch.object(settings, "PUBLISH_DAILY_LIMIT", 1):
            self.make_post(status=PostStatus.PUBLISHED, claimed_at=NOW - timedelta(hours=2))
            summary = self.go({"facebook": FakePublisher("facebook")})
        self.assertTrue(summary.daily_limit_reached)
        self.assertEqual(summary.outcomes, [])

    def test_dry_run_changes_nothing(self):
        post = self.make_post(platforms="facebook")
        fb = FakePublisher("facebook")
        summary = self.go({"facebook": fb}, dry_run=True)
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.APPROVED)
        self.assertIsNone(post.claimed_at)
        self.assertEqual(fb.calls, 0)
        self.assertEqual({(d.status, d.attempts) for d in post.deliveries}, {("pending", 0)})   # لم يُلمس شيء
        self.assertEqual(summary.outcomes[0].status, "dry-run")
        self.assertEqual(summary.outcomes[0].channels, ["facebook:111111"])


class StaleTests(Base_):
    def test_stale_reported_once_and_never_republished(self):
        post = self.make_post(status=PostStatus.PUBLISHING, claimed_at=NOW - timedelta(minutes=45))
        fb = FakePublisher("facebook")
        first = self.go({"facebook": fb})
        second = self.go({"facebook": fb})
        self.db.refresh(post)
        self.assertEqual(first.stale_post_ids, [post.id])
        self.assertEqual(second.stale_post_ids, [post.id])
        self.assertEqual(post.status, PostStatus.PUBLISHING)       # لا يُعاد نشره
        self.assertEqual(fb.calls, 0)
        self.assertEqual(len(self.db.scalars(select(models.PublishReport)).all()), 1)

    def test_recent_publishing_not_stale(self):
        self.make_post(status=PostStatus.PUBLISHING, claimed_at=NOW - timedelta(minutes=5))
        self.assertEqual(self.go({}).stale_post_ids, [])


class ApproveTests(Base_):
    def test_approve_sets_status_and_time(self):
        post = self.make_post(status=PostStatus.DRAFT, approved_at=None)
        post_publisher.approve_post(self.db, post.id, now=NOW)
        self.assertEqual((post.status, post.approved_at), (PostStatus.APPROVED, NOW))
        self.assertEqual(post.approval, models.PostApproval.APPROVED)

    def test_cannot_approve_published_or_publishing(self):
        for status in (PostStatus.PUBLISHED, PostStatus.PUBLISHING):
            post = self.make_post(status=status)
            with self.assertRaises(ConflictError):
                post_publisher.approve_post(self.db, post.id)

    def test_cannot_approve_without_channels(self):
        post = self.make_post(status=PostStatus.DRAFT, platforms=None)
        with self.assertRaises(InvalidInputError):
            post_publisher.approve_post(self.db, post.id)


if __name__ == "__main__":
    unittest.main()


class ListAndManualPublishTests(Base_):
    def test_published_and_unpublished_lists(self):
        pub = self.make_post(status=PostStatus.PUBLISHED)
        draft = self.make_post(status=PostStatus.DRAFT)
        failed = self.make_post(status=PostStatus.FAILED)
        archived = self.make_post(status=PostStatus.ARCHIVED)
        self.assertEqual([p.id for p in post_publisher.list_published(self.db)], [pub.id])
        self.assertEqual({p.id for p in post_publisher.list_unpublished(self.db)}, {draft.id, failed.id})
        self.assertNotIn(archived.id, {p.id for p in post_publisher.list_unpublished(self.db)})

    def test_claim_post_ignores_schedule_and_approval(self):
        post = self.make_post(status=PostStatus.DRAFT, scheduled_at=NOW + timedelta(days=3))
        claimed = post_publisher.claim_post(self.db, post.id, now=NOW)
        self.assertEqual((claimed.status, claimed.claimed_at), (PostStatus.PUBLISHING, NOW))

    def test_claim_post_rejects_published_publishing_and_second_claim(self):
        for status in (PostStatus.PUBLISHED, PostStatus.PUBLISHING, PostStatus.ARCHIVED):
            with self.assertRaises(ConflictError):
                post_publisher.claim_post(self.db, self.make_post(status=status).id)
        post = self.make_post()
        post_publisher.claim_post(self.db, post.id)
        with self.assertRaises(ConflictError):
            post_publisher.claim_post(self.db, post.id)

    def test_claim_post_errors(self):
        from app.services.errors import NotFoundError
        with self.assertRaises(NotFoundError):
            post_publisher.claim_post(self.db, 9999)
        with self.assertRaises(InvalidInputError):
            post_publisher.claim_post(self.db, self.make_post(platforms=None).id)

    def test_claimed_post_then_publishes(self):
        post = self.make_post(status=PostStatus.DRAFT, platforms="facebook")
        claimed = post_publisher.claim_post(self.db, post.id, now=NOW)
        outcome = run(post_publisher.publish_post(self.db, claimed, {"facebook": FakePublisher("facebook")}, NOW))
        self.assertEqual(outcome.status, "published")
