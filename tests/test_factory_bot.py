"""اختبارات تدفق شجرة بوت المصنع (قاعدة SQLite في الذاكرة، بلا شبكة).
تحتاج SQLAlchemy (من requirements.txt). التشغيل:  python -m unittest discover -s tests -v"""
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    HAVE_SQLALCHEMY = True
except ImportError:                     # pragma: no cover
    HAVE_SQLALCHEMY = False

from app.core.config import settings

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 16


def text(body):
    return {"type": "text", "text": {"body": body}}


def choice(cid, kind="button_reply"):
    return {"type": "interactive", "interactive": {"type": kind, kind: {"id": cid}}}


def image(media_id):
    return {"type": "image", "image": {"id": media_id}}


def video(media_id):
    return {"type": "video", "video": {"id": media_id}}


@unittest.skipUnless(HAVE_SQLALCHEMY, "SQLAlchemy غير مثبتة")
class FactoryBotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app import models
        from app.core.database import Base
        from app.services import factory_bot
        self.models, self.bot = models, factory_bot

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine, autoflush=False)()
        self.addCleanup(self.db.close)

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        (base / "products").mkdir()
        self.sent = []          # (نوع، نص، معرّفات)
        self.downloads = {}     # media_id -> bytes

        async def send_text(phone, body):
            self.sent.append(("text", body, []))

        async def send_buttons(phone, body, buttons):
            self.sent.append(("buttons", body, [b[0] for b in buttons]))

        async def send_list(phone, body, label, rows, title="x"):
            self.sent.append(("list", body, [r[0] for r in rows]))

        async def download(media_id):
            return self.downloads.get(media_id)

        async def sync(product_id, db):
            return {"status": "success"}

        for target, value in [
            (settings, ("MEDIA_ROOT", base / "products")), (settings, ("MEDIA_BACKUP_DIR", base / "backup")),
            (settings, ("IMAGE_BASE_URL", "https://hadjretbladi.com")),
            (settings, ("PRODUCT_LINK_TEMPLATE", "https://store.example/{sku}")),
        ]:
            p = mock.patch.object(target, *value)
            p.start()
            self.addCleanup(p.stop)
        for name, fn in [("send_whatsapp_message", send_text), ("send_whatsapp_buttons", send_buttons),
                         ("send_whatsapp_list", send_list), ("download_whatsapp_media", download),
                         ("sync_product_to_meta", sync)]:
            p = mock.patch.object(factory_bot, name, fn)
            p.start()
            self.addCleanup(p.stop)

        self.factory = models.Factory(name="مصنع النور", phone_number="213550000001", categories=["B"])
        self.other = models.Factory(name="مصنع آخر", phone_number="213550000002", categories=["F"])
        self.db.add_all([self.factory, self.other])
        self.db.commit()

    # --- أدوات
    async def say(self, message, factory=None):
        factory = factory or self.factory
        return await self.bot.handle(self.db, factory, factory.phone_number, message)

    def last(self):
        return self.sent[-1]

    def state(self, factory=None):
        s = self.bot.get_session(self.db, factory or self.factory)
        return s.state if s else None

    async def add_product(self, title="حجر قالمة", price="3200", n_images=2, video_id=None, avail="fb:av:in"):
        await self.say(choice("fb:add"))
        await self.say(choice("fb:sku:auto"))
        await self.say(text(title))
        await self.say(text(price))
        await self.say(choice("fb:skip"))
        for i in range(n_images):
            self.downloads[f"img{i}"] = JPEG if i % 2 == 0 else PNG
            await self.say(image(f"img{i}"))
        await self.say(choice("fb:done"))
        if video_id:
            self.downloads[video_id] = MP4
            await self.say(video(video_id))
        else:
            await self.say(choice("fb:skip"))
        await self.say(choice(avail))
        await self.say(choice("fb:ok"))

    def product(self, sku):
        return self.db.query(self.models.Product).filter_by(sku=sku).first()

    # --- القائمة الرئيسية
    async def test_any_text_shows_the_two_option_menu(self):
        await self.say(text("السلام عليكم"))
        kind, body, ids = self.last()
        self.assertEqual((kind, ids), ("buttons", ["fb:add", "fb:edit"]))
        self.assertIsNone(self.state())

    async def test_image_without_session_falls_through_to_legacy_flow(self):
        self.assertFalse(await self.say(image("x")))

    async def test_foreign_buttons_are_ignored(self):
        self.assertFalse(await self.say({"type": "interactive",
                                         "interactive": {"button_reply": {"id": "upd:yes:3"}}}))

    # --- الإضافة
    async def test_full_add_flow_single_category(self):
        await self.add_product(video_id="vid")
        p = self.product("B001")
        self.assertEqual((p.title, p.price, p.factory_id), ("حجر قالمة", 3200.0, self.factory.id))
        self.assertEqual(p.primary_media_url, "https://hadjretbladi.com/B001/1.jpg")
        self.assertEqual(p.additional_media_urls, ["https://hadjretbladi.com/B001/2.png"])
        self.assertEqual(p.video_url, "https://hadjretbladi.com/B001/video.mp4")
        self.assertEqual(p.availability, self.models.Availability.IN_STOCK)
        self.assertIn("حجر قالمة", p.description)            # وصف تلقائي عند التخطي
        self.assertIsNone(self.state())                      # المسودة نُظّفت
        self.assertIn("https://store.example/B001", self.sent[-1][1])
        self.assertEqual(self.sent[-1][2], ["fb:add", "fb:edit"])

    async def test_second_auto_sku_increments(self):
        await self.add_product()
        await self.add_product(title="حجر ثان")
        self.assertIsNotNone(self.product("B002"))

    async def test_multi_category_asks_for_category(self):
        self.factory.categories = ["B", "PK"]
        self.db.commit()
        await self.say(choice("fb:add"))
        self.assertEqual(self.state(), "add_cat")
        self.assertEqual(self.last()[2], ["fb:cat:B", "fb:cat:PK"])
        await self.say(choice("fb:cat:PK"))
        self.assertEqual(self.state(), "add_sku")
        self.assertIn("PK001", self.last()[1])
        await self.say(choice("fb:cat:B"))                    # زر قديم: يُتجاهل
        self.assertEqual(self.state(), "add_sku")

    async def test_cannot_pick_a_category_the_factory_does_not_own(self):
        self.factory.categories = ["B", "PK"]
        self.db.commit()
        await self.say(choice("fb:add"))
        await self.say(choice("fb:cat:F"))
        self.assertEqual(self.state(), "add_cat")

    async def test_factory_without_categories_is_blocked(self):
        self.factory.categories = []
        self.db.commit()
        await self.say(choice("fb:add"))
        self.assertIsNone(self.state())
        self.assertIn("تواصل مع الإدارة", self.last()[1])

    async def test_categories_derived_from_existing_products(self):
        await self.add_product()
        self.factory.categories = []
        self.db.commit()
        await self.say(choice("fb:add"))
        self.assertEqual(self.state(), "add_sku")

    async def test_manual_sku_rules(self):
        self.db.add(self.models.Product(sku="B005", title="t", price=1, primary_media_url="u",
                                        factory_id=self.other.id))
        self.db.add(self.models.Product(sku="B006", title="mine", price=1, primary_media_url="u",
                                        factory_id=self.factory.id))
        self.db.commit()
        await self.say(choice("fb:add"))
        await self.say(choice("fb:sku:manual"))
        self.assertEqual(self.state(), "add_sku_manual")
        for bad in ["b-1", "F001", "B005"]:                   # صيغة خاطئة / صنف غير صنفه / يخص مصنعاً آخر
            await self.say(text(bad))
            self.assertEqual(self.state(), "add_sku_manual", bad)
        await self.say(text("B006"))                          # موجود عنده: يعرض التعديل
        self.assertEqual(self.last()[2], ["fb:ep:B006"])
        await self.say(text("b014"))
        self.assertEqual(self.state(), "add_title")
        self.assertEqual(self.bot.get_session(self.db, self.factory).draft["sku"], "B014")

    async def test_validation_keeps_the_step(self):
        await self.say(choice("fb:add"))
        await self.say(choice("fb:sku:auto"))
        await self.say(text("x"))
        self.assertEqual(self.state(), "add_title")
        await self.say(text("حجر"))
        await self.say(text("مجاني"))
        self.assertEqual(self.state(), "add_price")
        await self.say(text("٣٢٠٠"))
        self.assertEqual(self.state(), "add_desc")

    async def test_done_without_images_is_refused(self):
        await self.say(choice("fb:add"))
        await self.say(choice("fb:sku:auto"))
        await self.say(text("حجر"))
        await self.say(text("100"))
        await self.say(choice("fb:skip"))
        await self.say(choice("fb:done"))
        self.assertEqual(self.state(), "add_images")

    async def test_images_arriving_together_are_all_kept(self):
        await self.say(choice("fb:add"))
        await self.say(choice("fb:sku:auto"))
        await self.say(text("حجر"))
        await self.say(text("100"))
        await self.say(choice("fb:skip"))
        await asyncio.gather(*(self.say(image(f"m{i}")) for i in range(5)))
        self.assertEqual(len(self.bot.get_session(self.db, self.factory).draft["images"]), 5)

    async def test_image_limit(self):
        await self.say(choice("fb:add"))
        await self.say(choice("fb:sku:auto"))
        await self.say(text("حجر"))
        await self.say(text("100"))
        await self.say(choice("fb:skip"))
        for i in range(self.bot.MAX_IMAGES + 2):
            await self.say(image(f"m{i}"))
        self.assertEqual(len(self.bot.get_session(self.db, self.factory).draft["images"]), self.bot.MAX_IMAGES)

    async def test_failed_download_keeps_draft_and_writes_nothing(self):
        await self.say(choice("fb:add"))
        await self.say(choice("fb:sku:auto"))
        await self.say(text("حجر"))
        await self.say(text("100"))
        await self.say(choice("fb:skip"))
        await self.say(image("missing"))                     # لا يوجد في downloads
        await self.say(choice("fb:done"))
        await self.say(choice("fb:skip"))
        await self.say(choice("fb:av:in"))
        await self.say(choice("fb:ok"))
        self.assertIsNone(self.product("B001"))
        self.assertEqual(self.state(), "add_images")
        from app.services import media
        self.assertFalse(media.has_media("B001"))

    async def test_cancel_and_back(self):
        await self.say(choice("fb:add"))
        await self.say(choice("fb:sku:auto"))
        await self.say(text("حجر"))
        self.assertEqual(self.state(), "add_price")
        await self.say(text("رجوع"))
        self.assertEqual(self.state(), "add_title")
        await self.say(text("رجوع"))
        self.assertEqual(self.state(), "add_sku")
        await self.say(text("رجوع"))                         # صنف واحد: يعود للقائمة
        self.assertIsNone(self.state())
        await self.say(choice("fb:add"))
        await self.say(text("إلغاء"))
        self.assertIsNone(self.state())
        self.assertEqual(self.db.query(self.models.Product).count(), 0)

    async def test_idle_session_expires(self):
        await self.say(choice("fb:add"))
        session = self.bot.get_session(self.db, self.factory)
        session.updated_at = datetime.utcnow() - timedelta(minutes=31)
        self.db.commit()
        await self.say(text("حجر"))                          # يُعامل كبداية جديدة
        self.assertEqual(self.last()[2], ["fb:add", "fb:edit"])
        self.assertIsNone(self.state())

    # --- التعديل
    async def test_edit_title_price_description_availability(self):
        await self.add_product()
        await self.say(choice("fb:edit"))
        self.assertEqual(self.last()[2], ["fb:ep:B001"])
        await self.say(choice("fb:ep:B001", "list_reply"))
        self.assertEqual(self.state(), "edit_field")
        for field, value in [("title", "اسم جديد"), ("price", "4100"), ("desc", "وصف جديد")]:
            await self.say(choice(f"fb:f:{field}", "list_reply"))
            await self.say(text(value))
            self.assertEqual(self.state(), "edit_field")
        await self.say(choice("fb:f:avail", "list_reply"))
        await self.say(choice("fb:av:out"))
        p = self.product("B001")
        self.db.refresh(p)
        self.assertEqual((p.title, p.price, p.description), ("اسم جديد", 4100.0, "وصف جديد"))
        self.assertEqual(p.availability, self.models.Availability.OUT_OF_STOCK)

    async def test_invalid_price_edit_is_rejected(self):
        await self.add_product()
        await self.say(text("B001"))                         # لا جلسة: قائمة
        await self.say(choice("fb:edit"))
        await self.say(text("b001"))                         # الرمز مكتوباً
        await self.say(choice("fb:f:price", "list_reply"))
        await self.say(text("-5"))
        self.assertEqual(self.state(), "edit_text")
        self.assertEqual(self.product("B001").price, 3200.0)

    async def test_factory_cannot_touch_another_factorys_product(self):
        self.db.add(self.models.Product(sku="F001", title="لغيري", price=10, primary_media_url="u",
                                        factory_id=self.other.id))
        self.db.commit()
        await self.say(choice("fb:edit"))
        self.assertIn("ليس لديك منتجات", self.last()[1])     # لا منتجات عنده: عودة للقائمة
        await self.say(choice("fb:ep:F001", "list_reply"))   # زر مفبرك
        self.assertIsNone(self.state())
        await self.add_product()
        await self.say(choice("fb:edit"))
        await self.say(text("F001"))
        self.assertEqual(self.state(), "edit_pick")
        self.assertEqual(self.product("F001").title, "لغيري")

    async def test_edit_list_paginates(self):
        for i in range(1, 12):
            self.db.add(self.models.Product(sku=f"B{i:03d}", title=f"p{i}", price=1, primary_media_url="u",
                                            factory_id=self.factory.id))
        self.db.commit()
        await self.say(choice("fb:edit"))
        ids = self.last()[2]
        self.assertEqual((len(ids), ids[-1]), (9, "fb:pg:1"))
        await self.say(choice("fb:pg:1", "list_reply"))
        ids = self.last()[2]
        self.assertEqual(ids, ["fb:ep:B009", "fb:ep:B010", "fb:ep:B011", "fb:pg:0"])

    async def test_image_add_replace_delete(self):
        await self.add_product()                             # صورتان: 1.jpg 2.png
        await self.say(choice("fb:ep:B001", "list_reply"))
        await self.say(choice("fb:f:images", "list_reply"))
        # إضافة
        self.downloads["new"] = JPEG
        await self.say(choice("fb:img:add"))
        await self.say(image("new"))
        await self.say(choice("fb:done"))
        p = self.product("B001")
        self.db.refresh(p)
        self.assertEqual(p.additional_media_urls,
                         ["https://hadjretbladi.com/B001/2.png", "https://hadjretbladi.com/B001/3.jpg"])
        # استبدال الرئيسية بصورة PNG
        await self.say(choice("fb:f:images", "list_reply"))
        self.downloads["rep"] = PNG
        await self.say(choice("fb:img:rep"))
        await self.say(text("9"))
        self.assertEqual(self.state(), "edit_img_rep_n")
        await self.say(text("1"))
        await self.say(image("rep"))
        self.db.refresh(p)
        self.assertEqual(p.primary_media_url, "https://hadjretbladi.com/B001/1.png")
        # حذف الأولى: يُعاد الترقيم
        await self.say(choice("fb:f:images", "list_reply"))
        await self.say(choice("fb:img:del"))
        await self.say(text("1"))
        self.db.refresh(p)
        self.assertEqual(p.primary_media_url, "https://hadjretbladi.com/B001/1.png")
        self.assertEqual(p.additional_media_urls, ["https://hadjretbladi.com/B001/2.jpg"])

    async def test_cannot_delete_the_last_image(self):
        await self.add_product(n_images=1)
        await self.say(choice("fb:ep:B001", "list_reply"))
        await self.say(choice("fb:f:images", "list_reply"))
        await self.say(choice("fb:img:del"))
        self.assertEqual(self.state(), "edit_img_menu")

    async def test_video_add_and_remove(self):
        await self.add_product()
        await self.say(choice("fb:ep:B001", "list_reply"))
        await self.say(choice("fb:f:video", "list_reply"))
        self.assertEqual(self.last()[2], ["fb:back"])        # لا زر إزالة بلا فيديو
        self.downloads["v"] = MP4
        await self.say(video("v"))
        p = self.product("B001")
        self.db.refresh(p)
        self.assertEqual(p.video_url, "https://hadjretbladi.com/B001/video.mp4")
        await self.say(choice("fb:f:video", "list_reply"))
        self.assertEqual(self.last()[2], ["fb:vid:rm", "fb:back"])
        await self.say(choice("fb:vid:rm"))
        self.db.refresh(p)
        self.assertIsNone(p.video_url)


if __name__ == "__main__":
    unittest.main()
