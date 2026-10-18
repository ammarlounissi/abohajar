"""عدة قنوات في المنصة الواحدة: تحديد الوجهات، النشر لكل قناة، إعادة المحاولة، والرجوع لمعرّف الإعدادات."""
import asyncio
import re
import unittest
from datetime import datetime, timedelta
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models, schemas
from app.core.config import settings
from app.core.database import Base
from app.models import PostStatus
from app.services import post_publisher
from app.services.social.base import Publisher, PublishError
from app.services.social.facebook import FacebookPublisher

NOW = datetime(2026, 10, 17, 7, 0, 0)


class RecordingPublisher(Publisher):
    """يسجّل (القناة) في قائمة مشتركة بين النسخ التي ينتجها for_channel."""

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

    def channel(self, platform, remote_id, active=True):
        self.db.add(models.SocialChannel(platform=platform, name=remote_id, remote_id=remote_id, is_active=active))
        self.db.commit()

    def post(self, platforms):
        post = models.Post(
            factory_id=1, media_type="image", media_urls=["https://x/1.jpg"], description="نص",
            status=PostStatus.APPROVED, platforms=platforms, approved_at=NOW - timedelta(hours=1),
        )
        self.db.add(post)
        self.db.commit()
        return post

    def publishers(self, fail_on=()):
        return {"facebook": RecordingPublisher("facebook", self.log, fail_on),
                "instagram": RecordingPublisher("instagram", self.log, fail_on)}

    def go(self, publishers, **kw):
        return asyncio.run(post_publisher.publish_due(self.db, publishers=publishers, now=NOW, **kw))


class ParsingTests(unittest.TestCase):
    def test_parse_targets(self):
        self.assertEqual(
            post_publisher.parse_targets("Facebook, instagram,facebook:123,facebook:123,telegram:@My_Chan"),
            [("facebook", None), ("instagram", None), ("facebook", "123"), ("telegram", "@My_Chan")],
        )
        self.assertEqual(post_publisher.parse_platforms("facebook:1,facebook:2,instagram"), ["facebook", "instagram"])

    def test_schema_pattern_accepts_channel_tokens(self):
        for ok in ("facebook", "facebook,instagram", "facebook:123456789012345,instagram", "telegram:-1001234567890",
                   "telegram:@my_channel"):
            self.assertTrue(re.match(schemas.PLATFORMS_PATTERN, ok), ok)
        for bad in ("Facebook", "facebook:", "facebook:a b", "facebook,,instagram", ":123"):
            self.assertFalse(re.match(schemas.PLATFORMS_PATTERN, bad), bad)


class ResolveTests(Base_):
    def test_platform_name_means_all_active_channels(self):
        self.channel("facebook", "111111"); self.channel("facebook", "222222")
        self.channel("facebook", "333333", active=False)
        targets = post_publisher.resolve_targets(self.db, self.post("facebook"))
        self.assertEqual([t.key for t in targets], [("facebook", "111111"), ("facebook", "222222")])

    def test_specific_channel(self):
        self.channel("facebook", "111111"); self.channel("facebook", "222222")
        targets = post_publisher.resolve_targets(self.db, self.post("facebook:222222"))
        self.assertEqual([t.key for t in targets], [("facebook", "222222")])
        self.assertIsNone(targets[0].error)

    def test_specific_channel_unknown_or_inactive_is_error(self):
        self.channel("facebook", "111111", active=False)
        for platforms in ("facebook:111111", "facebook:999999"):
            with self.subTest(platforms=platforms):
                (target,) = post_publisher.resolve_targets(self.db, self.post(platforms))
                self.assertTrue(target.error.startswith("channel_not_found_or_inactive"))

    def test_no_channels_registered_falls_back_to_settings(self):
        (target,) = post_publisher.resolve_targets(self.db, self.post("facebook"))
        self.assertEqual(target.key, ("facebook", ""))
        self.assertIsNone(target.error)

    def test_all_channels_disabled_does_not_fall_back_to_settings(self):
        self.channel("facebook", "111111", active=False)
        (target,) = post_publisher.resolve_targets(self.db, self.post("facebook"))
        self.assertEqual(target.error, "no_active_channel:facebook")

    def test_platforms_are_independent(self):
        self.channel("facebook", "111111")
        keys = [t.key for t in post_publisher.resolve_targets(self.db, self.post("facebook,instagram"))]
        self.assertEqual(keys, [("facebook", "111111"), ("instagram", "")])


class PublishTests(Base_):
    def test_publishes_to_every_active_channel_of_a_platform(self):
        self.channel("facebook", "111111"); self.channel("facebook", "222222"); self.channel("instagram", "999999")
        post = self.post("facebook,instagram")
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
        self.assertEqual({r.channel_remote_id for r in summary.outcomes[0].results}, {"111111", "222222", "999999"})

    def test_partial_failure_then_retry_only_failed_channel(self):
        self.channel("facebook", "111111"); self.channel("facebook", "222222")
        post = self.post("facebook")
        self.go(self.publishers(fail_on={"222222"}))
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.FAILED)
        self.assertIn("facebook:222222", post.error)
        report = self.db.query(models.PublishReport).one()
        self.assertTrue(report.message.startswith("[222222]"))

        self.log.clear()
        post_publisher.approve_post(self.db, post.id, NOW)
        self.go(self.publishers())
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.PUBLISHED)
        self.assertEqual(self.log, [("facebook", "222222")])      # الناجحة لم تُعَد
        self.assertEqual(len(post.deliveries), 2)

    def test_missing_channel_fails_without_calling_publisher(self):
        post = self.post("facebook:999999")
        self.go(self.publishers())
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.FAILED)
        self.assertEqual(self.log, [])
        self.assertIn("channel_not_found_or_inactive", post.error)

    def test_legacy_settings_path_unchanged(self):
        post = self.post("facebook")
        self.go(self.publishers())
        self.db.refresh(post)
        self.assertEqual(self.log, [("facebook", None)])
        (delivery,) = post.deliveries
        self.assertEqual(delivery.channel_remote_id, "")

    def test_dry_run_lists_each_channel_and_does_not_claim(self):
        self.channel("facebook", "111111"); self.channel("facebook", "222222")
        post = self.post("facebook")
        summary = self.go(self.publishers(), dry_run=True)
        self.db.refresh(post)
        self.assertEqual(post.status, PostStatus.APPROVED)
        self.assertEqual(summary.outcomes[0].platforms, ["facebook:111111", "facebook:222222"])
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
