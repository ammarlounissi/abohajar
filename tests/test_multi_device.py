"""حساب واحد من عدة أجهزة: كل جهاز يدخل بنفسه (كوكي جلسة خاص به)، ويرى الصفحة نفسها ومكتبتها وإعداداتها
ومسوداتها، وما يفعله جهاز يظهر في الآخر. وصور الهاتف كما تخرج من الكاميرا تُقبل.

الجهازان هنا عميلا TestClient منفصلان (لكل منهما كوكيز)، والدخول بمسار /auth/login الحقيقي لا بكوكي مصنوع."""
import io
from datetime import datetime, timedelta
from unittest import mock

from PIL import Image

from app.core.config import settings
from app.routers import auth
from app.services import page_appearance, push_notifications
from scripts import generate_vapid_keys
from tests.test_customer_workspace import BASE, SERVICE
from tests.test_post_drafts import DraftTestBase
from tests.test_push_notifications import FCM, subscription

EMAIL, PASSWORD = "owner@example.com", "secret-pass"
DESKTOP_PUSH, PHONE_PUSH = FCM + "desktop", FCM + "phone"


# ───── صور كما تخرج من الهواتف ─────

def _photo(size=(60, 40), color=(30, 140, 200)) -> Image.Image:
    return Image.new("RGB", size, color)


def multi_picture_jpeg() -> bytes:
    """JPEG متعدد الصور (MPO): كاميرات Android بوضع البورتريه أو العمق أو الصورة المتحركة تحفظ هكذا بامتداد .jpg."""
    buffer = io.BytesIO()
    _photo().save(buffer, "MPO", save_all=True, append_images=[_photo((30, 20), (0, 0, 0))])
    return buffer.getvalue()


def rotated_jpeg_with_location() -> bytes:
    """صورة هاتف مقلوبة بعلامة EXIF (Orientation=6: تُدار 90°) وفيها موقع GPS، كما تحفظها الكاميرا عادة."""
    exif = Image.Exif()
    exif[0x0112] = 6                                    # Orientation
    exif[0x8825] = {1: "N", 2: (36.0, 45.0, 0.0)}       # GPSInfo
    buffer = io.BytesIO()
    _photo((60, 40)).save(buffer, "JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def encoded(image_format: str) -> bytes:
    buffer = io.BytesIO()
    _photo().save(buffer, image_format)
    return buffer.getvalue()


class MultiDeviceTests(DraftTestBase):

    def setUp(self):
        super().setUp()
        self.app.include_router(auth.router, prefix="/auth")
        public_key, private_key = generate_vapid_keys.generate()
        self.sent = []
        for patcher in (
            mock.patch.object(settings, "SESSION_SECRET", "multi-device-secret"),
            mock.patch.object(settings, "VAPID_PUBLIC_KEY", public_key),
            mock.patch.object(settings, "VAPID_PRIVATE_KEY", private_key),
            mock.patch.object(push_notifications, "_post",
                              side_effect=lambda info, data, ttl: self.sent.append(info["endpoint"])),
            mock.patch.object(page_appearance, "fetch_page_appearance", return_value={}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        # الحاسوب ينشئ الحساب ويربط الصفحة، ثم يدخل الهاتف بالبريد وكلمة السر
        self.desktop = self.client()
        res = self.desktop.post("/auth/register", json={"email": EMAIL, "password": PASSWORD, "name": "صاحب الصفحة"})
        self.assertEqual(res.status_code, 201, res.text)
        self.user_id = res.json()["user"]["id"]
        self.link(self.user_id)
        self.phone = self.device()

    def device(self):
        """جهاز جديد: عميل بلا كوكيز يدخل بالحساب نفسه."""
        client = self.client()
        res = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertIn(settings.SESSION_COOKIE_NAME, client.cookies)
        return client

    def images(self, client):
        res = client.get(f"{BASE}/media", params={"media_type": "image"})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def stored_image(self, media):
        """الصورة كما حُفظت على القرص (بالرابط العام الذي يقرأ منه مولّد الفيديو وفيسبوك)."""
        res = self.desktop.get(media["url"].replace("http://testserver", ""))
        self.assertEqual(res.status_code, 200)
        return Image.open(io.BytesIO(res.content))

    # ───── الجلسة ─────

    def test_each_device_has_its_own_session_for_the_same_account(self):
        self.assertNotEqual(self.desktop.cookies[settings.SESSION_COOKIE_NAME], "")
        for client in (self.desktop, self.phone):
            me = client.get("/auth/me")
            self.assertEqual(me.status_code, 200, me.text)
            self.assertEqual(me.json()["user"]["id"], self.user_id)
            status = client.get(f"/auth/facebook/services/{SERVICE}").json()
            self.assertEqual(status["page"]["page_id"], "111")

    def test_logging_out_on_the_phone_keeps_the_desktop_signed_in(self):
        self.assertEqual(self.phone.post("/auth/logout").status_code, 200)
        self.assertEqual(self.phone.get(f"{BASE}/workspace").status_code, 401)
        self.assertEqual(self.desktop.get(f"{BASE}/workspace").status_code, 200)
        # ويعود الهاتف بدخول جديد
        self.assertEqual(self.device().get(f"{BASE}/workspace").status_code, 200)

    def test_a_third_device_sees_the_same_workspace(self):
        self.assertEqual(self.upload(self.phone).status_code, 201)
        tablet = self.device()
        views = [c.get(f"{BASE}/workspace").json() for c in (self.desktop, self.phone, tablet)]
        self.assertEqual(len({(v["page_id"], v["images_count"]) for v in views}), 1)
        self.assertEqual(views[0]["images_count"], 1)

    # ───── المكتبة ─────

    def test_library_changes_on_one_device_show_on_the_other(self):
        from_phone = self.upload(self.phone, name="IMG_0001.jpg", data=encoded("JPEG"), content_type="image/jpeg")
        self.assertEqual(from_phone.status_code, 201, from_phone.text)
        from_desktop = self.upload(self.desktop)
        self.assertEqual(from_desktop.status_code, 201, from_desktop.text)

        ids = {m["id"] for m in self.images(self.desktop)}
        self.assertEqual(ids, {m["id"] for m in self.images(self.phone)})
        self.assertEqual(len(ids), 2)

        self.assertEqual(self.desktop.delete(f"{BASE}/media/{from_phone.json()['id']}").status_code, 204)
        self.assertEqual([m["id"] for m in self.images(self.phone)], [from_desktop.json()["id"]])
        self.assertEqual(self.phone.delete(f"{BASE}/media/{from_phone.json()['id']}").status_code, 404)

    def test_phone_camera_photos_are_accepted(self):
        cases = [
            ("صورة متعددة (MPO) من كاميرا Android", "IMG_20261113_101010.jpg", multi_picture_jpeg(), "image/jpeg"),
            ("امتداد بحروف كبيرة", "IMG_0002.JPG", encoded("JPEG"), "image/jpeg"),
            ("امتداد jpeg", "photo.jpeg", encoded("JPEG"), "image/jpeg"),
            ("لقطة شاشة PNG", "Screenshot_20261113.png", encoded("PNG"), "image/png"),
            ("WebP", "image.webp", encoded("WEBP"), "image/webp"),
            ("PNG باسم .jpg (صورة محفوظة من تطبيق)", "IMG-WA0001.jpg", encoded("PNG"), "image/jpeg"),
            ("بلا امتداد (منتقي الصور في Android)", "1000012345", encoded("JPEG"), "image/jpeg"),
            ("بلا نوع من المتصفح", "photo.jpg", encoded("JPEG"), "application/octet-stream"),
        ]
        for label, name, data, content_type in cases:
            with self.subTest(label):
                res = self.upload(self.phone, name=name, data=data, content_type=content_type)
                self.assertEqual(res.status_code, 201, f"{label}: {res.text}")
        self.assertEqual(len(self.images(self.desktop)), len(cases))

    def test_stored_photo_is_upright_without_location_and_a_plain_format(self):
        res = self.upload(self.phone, name="IMG_0003.jpg", data=rotated_jpeg_with_location(), content_type="image/jpeg")
        self.assertEqual(res.status_code, 201, res.text)
        with self.stored_image(res.json()) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (40, 60))                 # أُديرت كما تعرضها الكاميرا
            exif = image.getexif()
            self.assertNotIn(0x0112, exif)
            self.assertNotIn(0x8825, exif)                          # لا موقع في الصورة العامة

        res = self.upload(self.phone, name="IMG_0004.jpg", data=multi_picture_jpeg(), content_type="image/jpeg")
        with self.stored_image(res.json()) as image:
            self.assertEqual(image.format, "JPEG")                  # MPO صار JPEG عادياً بصورته الأولى
            self.assertEqual(image.size, (60, 40))

        res = self.upload(self.phone, name="IMG-WA0002.jpg", data=encoded("PNG"), content_type="image/jpeg")
        self.assertTrue(res.json()["url"].endswith(".png"))        # الامتداد المحفوظ من المحتوى لا من الاسم

    def test_unsupported_phone_formats_get_a_clear_message(self):
        res = self.upload(self.phone, name="IMG_0005.HEIC", data=b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64,
                          content_type="image/heic")
        self.assertEqual(res.status_code, 422)
        self.assertIn("HEIC", res.json()["detail"])
        res = self.upload(self.phone, name="notes.jpg", data=b"not an image", content_type="image/jpeg")
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["detail"], "الملف ليس صورة صالحة")
        self.assertEqual(self.images(self.desktop), [])

    # ───── الإعدادات والنص ─────

    def test_page_settings_edited_on_the_phone_appear_on_the_desktop(self):
        res = self.phone.patch(f"{BASE}/profile", json={
            "activity": "مخبزة: خبز تقليدي", "whatsapp": "0550123456", "hashtags": ["خبز"],
        })
        self.assertEqual(res.status_code, 200, res.text)
        profile = self.desktop.get(f"{BASE}/profile").json()
        self.assertEqual((profile["activity"], profile["whatsapp"], profile["hashtags"]),
                         ("مخبزة: خبز تقليدي", "+213550123456", ["#خبز"]))

    def test_caption_requested_on_the_phone_is_followed_on_the_desktop(self):
        self.phone.patch(f"{BASE}/profile", json={"activity": "مخبزة: خبز تقليدي"})
        job = self.phone.post(f"{BASE}/captions")
        self.assertEqual(job.status_code, 202, job.text)
        # طلب ثانٍ من الحاسوب أثناء الكتابة يعيد المهمة نفسها لا مهمة جديدة
        self.assertEqual(self.desktop.post(f"{BASE}/captions").json()["id"], job.json()["id"])
        self.write_captions()
        done = self.desktop.get(f"{BASE}/captions/{job.json()['id']}").json()
        self.assertEqual(done["status"], "succeeded")
        self.assertTrue(done["caption"])

    # ───── الفيديو ─────

    def test_post_scheduled_on_the_phone_is_edited_and_approved_on_the_desktop(self):
        """منشور واحد (توليد فيديو واحد) بمراحله: يُجدول من الهاتف، ويُعدَّل ويُوافق عليه من الحاسوب،
        ويأخذ الهاتف بعده أقرب موعد تالٍ (القيود على الصفحة لا على الجهاز)."""
        for _ in range(4):
            self.upload(self.phone)
        self.assertEqual(self.desktop.patch(f"{BASE}/profile", json={"activity": "مخبزة"}).status_code, 200)
        created = self.phone.post(f"{BASE}/drafts/one", json={})
        self.assertEqual(created.status_code, 201, created.text)
        draft_id = created.json()["id"]
        self.write_captions()

        res = self.desktop.patch(f"{BASE}/drafts/{draft_id}", json={"caption": "من الحاسوب"})
        self.assertEqual(res.status_code, 200, res.text)
        res = self.desktop.post(f"{BASE}/drafts/approve", json={"ids": [draft_id]})
        self.assertEqual(res.status_code, 200, res.text)
        self.render_all()
        seen = next(d for d in self.drafts(self.phone) if d["id"] == draft_id)
        self.assertEqual((seen["status"], seen["caption"]), ("scheduled", "من الحاسوب"))
        self.assertIsNotNone(seen["video_url"])

        second = self.phone.post(f"{BASE}/drafts/one", json={}).json()
        gap = (datetime.fromisoformat(second["scheduled_at"].replace("Z", "+00:00"))
               - datetime.fromisoformat(seen["scheduled_at"].replace("Z", "+00:00")))
        self.assertGreaterEqual(gap, timedelta(hours=3))

    # ───── النشر المجدول ─────

    def test_drafts_generated_on_one_device_are_reviewed_on_the_other(self):
        for _ in range(4):
            self.upload(self.phone)
        self.phone.patch(f"{BASE}/profile", json={"activity": "مخبزة: خبز تقليدي"})
        self.generate(self.desktop, count=2)
        self.write_captions()
        drafts = self.drafts(self.phone)
        self.assertEqual([d["status"] for d in drafts], ["ready", "ready"])

        edited = self.phone.patch(f"{BASE}/drafts/{drafts[0]['id']}", json={"caption": "نص من الهاتف"})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(self.drafts(self.desktop)[0]["caption"], "نص من الهاتف")

        approved = self.desktop.post(f"{BASE}/drafts/approve", json={"ids": [drafts[0]["id"]]})
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(self.drafts(self.phone)[0]["status"], "approved")

        self.assertEqual(self.phone.delete(f"{BASE}/drafts/{drafts[1]['id']}").status_code, 204)
        restored = self.desktop.post(f"{BASE}/drafts/{drafts[1]['id']}/restore")
        self.assertEqual(restored.status_code, 200, restored.text)

    # ───── الإشعارات ─────

    def test_notifications_are_per_device(self):
        for client, endpoint in ((self.desktop, DESKTOP_PUSH), (self.phone, PHONE_PUSH)):
            res = client.post(f"{BASE}/push/subscriptions", json=subscription(endpoint))
            self.assertEqual(res.status_code, 200, res.text)

        phone_view = self.phone.get(f"{BASE}/push", params={"endpoint": PHONE_PUSH}).json()
        self.assertEqual((phone_view["devices"], phone_view["subscribed"]), (2, True))

        # الإشعار التجريبي يصل إلى الجهاز الذي طلبه وحده
        self.assertTrue(self.phone.post(f"{BASE}/push/test", json={"endpoint": PHONE_PUSH}).json()["sent"])
        self.assertEqual(self.sent, [PHONE_PUSH])

        # إيقاف الإشعارات على الهاتف لا يمسّ الحاسوب
        self.assertEqual(self.phone.post(f"{BASE}/push/unsubscribe", json={"endpoint": PHONE_PUSH}).status_code, 204)
        desktop_view = self.desktop.get(f"{BASE}/push", params={"endpoint": DESKTOP_PUSH}).json()
        self.assertEqual((desktop_view["devices"], desktop_view["subscribed"]), (1, True))

    # ───── الرئيسية ─────

    def test_dashboard_home_is_the_same_on_every_device(self):
        self.upload(self.phone)
        self.phone.patch(f"{BASE}/profile", json={"activity": "مخبزة: خبز تقليدي"})
        views = [c.get(f"{BASE}/overview") for c in (self.desktop, self.phone)]
        for res in views:
            self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(views[0].json(), views[1].json())
