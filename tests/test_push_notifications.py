"""إشعارات المتصفح: اشتراك أجهزة الصفحة من اللوحة، والإرسال إليها، وحذف المنتهية. بلا شبكة (_post مُستبدَل)."""
import asyncio
import base64
import json
import os
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException

from app import models
from app.core.config import settings
from app.models import PostStatus
from app.services import post_publisher, push_notifications
from scripts import generate_vapid_keys
from tests.test_customer_workspace import BASE, SERVICE, WorkspaceTestBase
from tests.test_post_publisher import NOW, FakePublisher

PUSH = f"{BASE}/push"
FCM = "https://fcm.googleapis.com/fcm/send/"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def device_keys() -> dict:
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
    )
    return {"p256dh": b64url(point), "auth": b64url(os.urandom(16))}


def subscription(endpoint=FCM + "device-1") -> dict:
    return {"endpoint": endpoint, "expirationTime": None, "keys": device_keys()}


def rejected(code: int) -> WebPushException:
    return WebPushException("rejected", response=mock.Mock(status_code=code))


class PushTestBase(WorkspaceTestBase):
    def setUp(self):
        super().setUp()
        self.public_key, private_key = generate_vapid_keys.generate()
        for name, value in (("VAPID_PUBLIC_KEY", self.public_key), ("VAPID_PRIVATE_KEY", private_key)):
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sent = []            # (endpoint, الحمولة) لكل طلب إلى خدمة الدفع
        self.failures = {}        # endpoint -> استثناء يرفعه الإرسال إليه
        self.real_post = push_notifications._post
        patcher = mock.patch.object(push_notifications, "_post", side_effect=self._fake_post)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_post(self, info, data, ttl):
        if info["endpoint"] in self.failures:
            raise self.failures[info["endpoint"]]
        self.sent.append((info["endpoint"], json.loads(data)))

    def subscribe(self, client, endpoint=FCM + "device-1"):
        res = client.post(f"{PUSH}/subscriptions", json=subscription(endpoint))
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def rows(self):
        with self.Session() as db:
            return db.query(models.PushSubscription).order_by(models.PushSubscription.id).all()


class PushSubscriptionTests(PushTestBase):
    def test_requires_login_and_a_linked_page(self):
        for method, path, body in (("get", "", None), ("post", "/subscriptions", subscription()),
                                   ("post", "/unsubscribe", {"endpoint": FCM}), ("post", "/test", {"endpoint": FCM})):
            with self.subTest(path=path):
                kwargs = {"json": body} if body is not None else {}
                self.assertEqual(getattr(self.client(), method)(f"{PUSH}{path}", **kwargs).status_code, 401)
                self.assertEqual(getattr(self.client(1), method)(f"{PUSH}{path}", **kwargs).status_code, 409)

    def test_status_subscribe_and_unsubscribe(self):
        self.link(1)
        client = self.client(1)
        status = client.get(PUSH).json()
        self.assertEqual(status, {"available": True, "public_key": self.public_key, "devices": 0, "subscribed": False})
        self.assertEqual(self.subscribe(client)["subscribed"], True)
        self.subscribe(client)                                  # تكراره يحدّث الصف نفسه
        self.assertEqual(len(self.rows()), 1)
        row = self.rows()[0]
        self.assertEqual((row.factory_id, row.service_key), (self.factory_of(1), SERVICE))
        self.assertTrue(client.get(PUSH, params={"endpoint": FCM + "device-1"}).json()["subscribed"])
        self.assertFalse(client.get(PUSH, params={"endpoint": FCM + "other"}).json()["subscribed"])
        self.assertEqual(client.post(f"{PUSH}/unsubscribe", json={"endpoint": FCM + "device-1"}).status_code, 204)
        self.assertEqual(client.post(f"{PUSH}/unsubscribe", json={"endpoint": FCM + "device-1"}).status_code, 204)
        self.assertEqual(self.rows(), [])

    def test_disabled_on_the_server_without_keys(self):
        self.link(1)
        client = self.client(1)
        with mock.patch.object(settings, "VAPID_PRIVATE_KEY", ""):
            self.assertEqual(client.get(PUSH).json()["available"], False)
            self.assertIsNone(client.get(PUSH).json()["public_key"])
            self.assertEqual(client.post(f"{PUSH}/subscriptions", json=subscription()).status_code, 409)

    def test_only_known_push_services_and_valid_keys(self):
        self.link(1)
        client = self.client(1)
        for endpoint in ("http://fcm.googleapis.com/fcm/send/x", "https://localhost:8000/x", "https://169.254.169.254/",
                         "https://fcm.googleapis.com.evil.com/x", "https://evil.com/fcm.googleapis.com",
                         "https://user@fcm.googleapis.com/x", "https://fcm.googleapis.com:8443/x"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(client.post(f"{PUSH}/subscriptions", json=subscription(endpoint)).status_code, 422)
        for endpoint in ("https://updates.push.services.mozilla.com/wpush/v2/x", "https://web.push.apple.com/x",
                         "https://wns2-par02p.notify.windows.com/w/?token=x"):
            self.subscribe(client, endpoint)
        bad = subscription()
        bad["keys"]["p256dh"] = b64url(b"\x04" + b"0" * 10)
        self.assertEqual(client.post(f"{PUSH}/subscriptions", json=bad).status_code, 422)
        bad = subscription()
        bad["keys"]["auth"] = "***"
        self.assertEqual(client.post(f"{PUSH}/subscriptions", json=bad).status_code, 422)
        self.assertEqual(len(self.rows()), 3)

    def test_devices_belong_to_the_page_not_shared_between_users(self):
        self.link(1, page_id="111")
        self.link(2, page_id="222")
        first, second = self.client(1), self.client(2)
        self.subscribe(first)
        self.assertEqual(second.get(PUSH).json()["devices"], 0)
        self.assertFalse(second.get(PUSH, params={"endpoint": FCM + "device-1"}).json()["subscribed"])
        self.assertEqual(second.post(f"{PUSH}/test", json={"endpoint": FCM + "device-1"}).status_code, 404)
        second.post(f"{PUSH}/unsubscribe", json={"endpoint": FCM + "device-1"})
        self.assertEqual(len(self.rows()), 1)                   # لا يحذف جهاز صفحة غيره
        self.subscribe(second)                                   # المتصفح نفسه لصفحتين: صفّان
        self.assertEqual(len(self.rows()), 2)

    def test_oldest_device_is_dropped_above_the_limit(self):
        self.link(1)
        client = self.client(1)
        with mock.patch.object(push_notifications, "MAX_DEVICES", 2):
            for n in range(3):
                self.subscribe(client, f"{FCM}device-{n}")
        self.assertEqual([row.endpoint for row in self.rows()], [f"{FCM}device-1", f"{FCM}device-2"])

    def test_test_notification_goes_to_this_device_only(self):
        self.link(1)
        client = self.client(1)
        self.subscribe(client, FCM + "phone")
        self.subscribe(client, FCM + "laptop")
        res = client.post(f"{PUSH}/test", json={"endpoint": FCM + "phone"})
        self.assertEqual(res.json(), {"sent": True})
        self.assertEqual([endpoint for endpoint, _ in self.sent], [FCM + "phone"])
        payload = self.sent[0][1]
        self.assertEqual(payload["title"], push_notifications.TEST_MESSAGE_TITLE)
        self.assertEqual(payload["url"], f"/services/{SERVICE}/dashboard/")
        self.assertEqual(client.post(f"{PUSH}/test", json={"endpoint": FCM + "tablet"}).status_code, 404)


class PushDeliveryTests(PushTestBase):
    def setUp(self):
        super().setUp()
        self.link(1)
        self.factory_id = self.factory_of(1)

    def notify(self, message=None):
        with self.Session() as db:
            return push_notifications.notify(db, self.factory_id, message or push_notifications.drafts_ready(3))

    def test_sends_to_every_device_with_the_dashboard_link(self):
        client = self.client(1)
        self.subscribe(client, FCM + "a")
        self.subscribe(client, FCM + "b")
        self.assertEqual(self.notify(), 2)
        self.assertEqual(sorted(e for e, _ in self.sent), [FCM + "a", FCM + "b"])
        payload = self.sent[0][1]
        self.assertEqual(payload["url"], f"/services/{SERVICE}/dashboard/schedule/")
        self.assertEqual(payload["tag"], "drafts-ready")
        self.assertIn("3", payload["body"])
        self.assertTrue(all(row.last_sent_at for row in self.rows()))

    def test_expired_device_is_removed_and_others_still_receive(self):
        client = self.client(1)
        self.subscribe(client, FCM + "gone")
        self.subscribe(client, FCM + "ok")
        self.failures[FCM + "gone"] = rejected(410)
        self.assertEqual(self.notify(), 1)
        self.assertEqual([row.endpoint for row in self.rows()], [FCM + "ok"])

    def test_repeated_failures_remove_the_device_and_success_resets(self):
        self.subscribe(self.client(1))
        self.failures[FCM + "device-1"] = rejected(500)
        for _ in range(push_notifications.MAX_FAILURES - 1):
            self.assertEqual(self.notify(), 0)
        self.assertEqual(self.rows()[0].failures, push_notifications.MAX_FAILURES - 1)
        del self.failures[FCM + "device-1"]
        self.notify()
        self.assertEqual(self.rows()[0].failures, 0)
        self.failures[FCM + "device-1"] = RuntimeError("network down")
        for _ in range(push_notifications.MAX_FAILURES):
            self.notify()
        self.assertEqual(self.rows(), [])

    def test_nothing_is_sent_without_keys_or_devices(self):
        self.assertEqual(self.notify(), 0)
        self.subscribe(self.client(1))
        with mock.patch.object(settings, "VAPID_PUBLIC_KEY", ""):
            self.assertEqual(self.notify(), 0)
        self.assertEqual(self.sent, [])

    def test_real_sender_signs_for_the_push_service_origin(self):
        """_post الحقيقي: VAPID بمفتاح الخادم، والجمهور (aud) أصل خدمة الدفع لا العنوان كله."""
        with mock.patch.object(push_notifications, "webpush") as webpush:
            self.real_post({"endpoint": FCM + "x", "keys": device_keys()}, "{}", 60)
        kwargs = webpush.call_args.kwargs
        self.assertEqual(kwargs["vapid_claims"]["aud"], "https://fcm.googleapis.com")
        self.assertEqual(kwargs["vapid_private_key"], settings.VAPID_PRIVATE_KEY)
        self.assertEqual(kwargs["ttl"], 60)

    def test_publisher_notifies_the_page_of_the_result(self):
        self.subscribe(self.client(1))
        with self.Session() as db:
            channel_id = db.query(models.SocialChannel.id).scalar()
            post = models.Post(factory_id=self.factory_id, media_type="reel", media_urls=["https://x/v.mp4"],
                               description="\nخبز الدار ساخن كل صباح\nاطلبوا الآن", status=PostStatus.PUBLISHING)
            post.deliveries = [models.PostDelivery(channel_id=channel_id, status="pending", attempts=0)]
            db.add(post)
            db.commit()
            outcome = asyncio.run(post_publisher.publish_post(db, post, {"facebook": FakePublisher("facebook")}, NOW))
        self.assertEqual(outcome.status, "published")
        payload = self.sent[-1][1]
        self.assertEqual(payload["title"], "نُشر منشورك على صفحتك")
        self.assertEqual(payload["body"], "خبز الدار ساخن كل صباح")
        self.assertEqual(payload["url"], f"/services/{SERVICE}/dashboard/")
