"""قنوات النشر بمعرّفها الداخلي: تحديد قنوات المنشور (set_channels)، النشر لكل قناة، إعادة المحاولة،
القنوات المعطّلة، وحماية القناة من الحذف. بلا شبكة: قاعدة SQLite في الذاكرة ومحوّلات وهمية."""
import asyncio
import unittest
from datetime import datetime, timedelta
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.config import settings
from app.core.database import Base
from app.models import PostStatus
from app.services import post_publisher
from app.services import social_channels as channel_service
from app.services.errors import ConflictError, InvalidInputError, NotFoundError
from app.services.social.base import Publisher, PublishError
from app.services.social.facebook import FacebookPublisher

NOW = datetime(2026, 10, 17, 7, 0, 0)


class RecordingPublisher(Publisher):
    """يسجّل (المنصة، المعرّف الأصلي للقناة) في قائمة مشتركة بين النسخ التي ينتجها for_channel."""

    def __init__(self, platform, log, fail_on=()):
        self.platform = platform
        self.log = log
        self.fail_on = set(fail_on)

    def validate(self, post):
        return None

    async def _publish(self, post):
        self.log.append((self.platform, self.channel_id))
        if self.channel_id in self.fail_on:
            raise PublishError(models.ReportKind.MEDIA, "bad_media")
        return f"{self.platform}-{self.channel_id}-{post.id}"


class Base_(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(self.db.close)
        self.db.add(models.Factory(id=1, name="مصنع", phone_number="213550000000"))
        self.db.commit()
        for name, value in (("PUBLISH_DAILY_LIMIT", 50), ("PUBLISH_STALE_MINUTES", 30)):
            p = mock.patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.log = []

    def channel(self, platform, remote_id, active=True) -> int:
        channel = models.SocialChannel(
            factory_id=1, platform=platform, name=remote_id, remote_id=remote_id, is_active=active,
        )
        self.db.add(channel)
        self.db.commit()
        return channel.id

    def post(self, *channel_ids, status=PostStatus.APPROVED):
        post = models.Post(
            factory_id=1, media_type="image", media_urls=["https://x/1.jpg"], description="نص",
            status=status, approved_at=NOW - timedelta(hours=1),
        )
        self.db.add(post)
        self.db.commit()
        if channel_ids:
            post_publisher.set_channels(self.db, post, channel_ids)
        return post

    def publishers(self, fail_on=()):
        return {"facebook": RecordingPublisher("facebook", self.log, fail_on),
                "instagram": RecordingPublisher("instagram", self.log, fail_on)}

    def go(self, publishers, **kw):
        return asyncio.run(post_publisher.publish_due(self.db, publishers=publishers, now=NOW, **kw))


class SetChannelsTests(Base_):
    def test_creates_pending_deliveries_in_input_order_without_duplicates(self):
        a, b = self.channel("facebook", "111111"), self.channel("instagram", "999999")
        post = self.post(b, a, b)
        self.assertEqual([(d.channel_id, d.status) for d in post.deliveries], [(b, "pending"), (a, "pending")])
        self.assertEqual([d.platform for d in post.deliveries], ["instagram", "facebook"])    # من القناة لا من المنشور

    def test_unknown_and_inactive_channels_are_rejected(self):
        ok = self.channel("facebook", "111111")
        off = self.channel("facebook", "222222", active=False)
        post = self.post()
        with self.assertRaises(NotFoundError):
            post_publisher.set_channels(self.db, post, [ok, 9999])
        with self.assertRaises(InvalidInputError):
            post_publisher.set_channels(self.db, post, [ok, off])
        self.assertEqual(post.deliveries, [])        # الطلب المرفوض لا يترك أثراً

    def test_channels_from_another_factory_are_rejected(self):
        self.db.add(models.Factory(id=2, name="مصنع آخر", phone_number="213550000001"))
        self.db.commit()
        channel = models.SocialChannel(factory_id=2, platform="facebook", name="صفحة", remote_id="333333")
        self.db.add(channel)
        self.db.commit()
        with self.assertRaises(InvalidInputError):
            post_publisher.set_channels(self.db, self.post(), [channel.id])

    def test_replace_drops_pending_and_failed_but_keeps_published(self):
        a, b, c = (self.channel("facebook", x) for x in ("111111", "222222", "333333"))
        post = self.post(a, b, c)
        by_channel = {d.channel_id: d for d in post.deliveries}
        by_channel[a].status = "published"
        by_channel[b].status = "failed"
        self.db.commit()
        post_publisher.set_channels(self.db, post, [c])
        self.assertEqual({d.channel_id for d in post.deliveries}, {a, c})     # الناجحة تبقى، الفاشلة حُذفت

    def test_empty_list_clears_pending(self):
        a = self.channel("facebook", "111111")
        post = self.post(a)
        post_publisher.set_channels(self.db, post, [])
        self.assertEqual(post.deliveries, [])

    def test_published_or_publishing_post_cannot_change_channels(self):
        a = self.channel("facebook", "111111")
        for status in (PostStatus.PUBLISHED, PostStatus.PUBLISHING, PostStatus.ARCHIVED):
            with self.assertRaises(ConflictError):
                post_publisher.set_channels(self.db, self.post(status=status), [a])

    def test_active_channel_ids(self):
        fb, ig = self.channel("facebook", "111111"), self.channel("instagram", "222222")
        self.channel("facebook", "333333", active=False)
        self.assertEqual(post_publisher.active_channel_ids(self.db, 1), [fb, ig])
        self.assertEqual(post_publisher.active_channel_ids(self.db, 1, "instagram"), [ig])

    def test_channel_with_deliveries_cannot_be_deleted(self):
        a, b = self.channel("facebook", "111111"), self.channel("facebook", "222222")
        self.post(a)
        with self.assertRaises(ConflictError):
            channel_service.delete_channel(self.db, a)
        channel_service.delete_channel(self.db, b)       # بلا منشورات: يُحذف


class CaptionTests(Base_):
    class CaptionPublisher(Publisher):
        def __init__(self, platform, seen):
            self.platform, self.seen = platform, seen

        def validate(self, post):
            return None

        async def _publish(self, post):
            self.seen[self.channel_id] = post.description      # المعرّف الأصلي للقناة
            return "ok"

    def test_each_channel_publishes_its_own_caption_else_the_post_text(self):
        fb, ig, tg = self.channel("facebook", "111111"), self.channel("instagram", "222222"), self.channel("facebook", "333333")
        post = self.post(fb, ig, tg)
        by_channel = {d.channel_id: d for d in post.deliveries}
        by_channel[fb].caption = "نص فيسبوك"
        by_channel[ig].caption = "نص إنستغرام"
        self.db.commit()
        seen = {}
        asyncio.run(post_publisher.publish_due(
            self.db, publishers={"facebook": self.CaptionPublisher("facebook", seen),
                                 "instagram": self.CaptionPublisher("instagram", seen)}, now=NOW))
        self.assertEqual(seen, {"111111": "نص فيسبوك", "222222": "نص إنستغرام", "333333": "نص"})   # الأخيرة بلا نص خاص
        self.assertEqual(by_channel[fb].caption, "نص فيسبوك")      # النص المنشور محفوظ كما نُشر


class PublishTests(Base_):
    def test_publishes_to_every_selected_channel(self):
        f1, f2, i1 = self.channel("facebook", "111111"), self.channel("facebook", "222222"), self.channel("instagram", "999999")
        post = self.post(f1, f2, i1)
        summary = self.go(self.publishers())
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.PUBLISHED)
        self.assertEqual(sorted(self.log), [("facebook", "111111"), ("facebook", "222222"), ("instagram", "999999")])
        self.assertEqual(
            sorted((d.platform, d.channel_remote_id, d.status) for d in post.deliveries),
            [("facebook", "111111", "published"), ("facebook", "222222", "published"),
             ("instagram", "999999", "published")],
        )
        self.assertEqual(summary.failures, 0)
        self.assertEqual({r.channel_id for r in summary.outcomes[0].results}, {f1, f2, i1})
        self.assertEqual({r.channel_label for r in summary.outcomes[0].results},
                         {"facebook:111111", "facebook:222222", "instagram:999999"})

    def test_unselected_channels_are_not_used(self):
        f1 = self.channel("facebook", "111111")
        self.channel("facebook", "222222")
        self.post(f1)
        self.go(self.publishers())
        self.assertEqual(self.log, [("facebook", "111111")])

    def test_partial_failure_then_retry_only_failed_channel(self):
        f1, f2 = self.channel("facebook", "111111"), self.channel("facebook", "222222")
        post = self.post(f1, f2)
        self.go(self.publishers(fail_on={"222222"}))
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.FAILED)
        self.assertIn("facebook:222222", post.error)
        report = self.db.query(models.PublishReport).one()
        self.assertTrue(report.message.startswith("[222222]"))
        self.assertEqual(report.platform, "facebook")

        self.log.clear()
        post_publisher.approve_post(self.db, post.id, NOW)
        self.go(self.publishers())
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.PUBLISHED)
        self.assertEqual(self.log, [("facebook", "222222")])      # الناجحة لم تُعَد
        self.assertEqual(len(post.deliveries), 2)

    def test_channel_disabled_after_assignment_fails_without_calling_publisher(self):
        a, b = self.channel("facebook", "111111"), self.channel("facebook", "222222")
        post = self.post(a, b)
        channel_service.update_channel(self.db, b, {"is_active": False})
        self.go(self.publishers())
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.FAILED)
        self.assertEqual(self.log, [("facebook", "111111")])
        self.assertIn("channel_inactive:facebook:222222", post.error)

    def test_no_fallback_to_settings_ids(self):
        post = self.post()       # بلا قنوات: لا يرجع الناشر إلى META_PAGE_ID
        with mock.patch.object(settings, "META_PAGE_ID", "777777"):
            self.go(self.publishers())
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.FAILED)
        self.assertEqual(post.error, "no_channels")
        self.assertEqual(self.log, [])

    def test_dry_run_lists_each_channel_and_does_not_claim(self):
        f1, f2 = self.channel("facebook", "111111"), self.channel("facebook", "222222")
        post = self.post(f1, f2)
        summary = self.go(self.publishers(), dry_run=True)
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.APPROVED)
        self.assertEqual(summary.outcomes[0].channels, ["facebook:111111", "facebook:222222"])
        self.assertTrue(all(r.ok for r in summary.outcomes[0].results))
        self.assertEqual(self.log, [])


class PublisherChannelTests(unittest.TestCase):
    def test_for_channel_overrides_settings_id_and_resets_page_token(self):
        base = FacebookPublisher()
        base._page_token = "OLD"
        clone = base.for_channel("555555")
        self.assertEqual(clone._page_id, "555555")
        self.assertIsNone(clone._page_token)
        self.assertEqual(base._page_token, "OLD")
        self.assertIsNone(base.channel_id)

    def test_channel_satisfies_missing_settings_id(self):
        post = mock.Mock(media_type="text", media_urls=[], description="x")
        with mock.patch.object(settings, "META_ACCESS_TOKEN", "T"), mock.patch.object(settings, "META_PAGE_ID", ""):
            self.assertEqual(FacebookPublisher().validate(post).message, "config_missing:META_PAGE_ID")
            self.assertIsNone(FacebookPublisher().for_channel("555555").validate(post))


if __name__ == "__main__":
    unittest.main()
