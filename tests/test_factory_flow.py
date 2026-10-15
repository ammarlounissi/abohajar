"""اختبار سير محادثة المصنع بقناة وهمية، دون قاعدة بيانات ولا شبكة.
القناة الوهمية تثبت أن السير لا يعتمد على واتساب: كل الاختبارات تعمل بقناتي whatsapp وtelegram."""
import unittest
from types import SimpleNamespace
from unittest import mock

from app.channels import factory_flow, texts
from app.channels.base import EventKind, IncomingEvent
from app.services import factories, product_intake
from app.services.import_job import ImportBusy, ImportJobResult
from app.services.import_report import ImportConfigError, ImportReport

FACTORY = SimpleNamespace(id=1, name="مصنع الاختبار")


class FakeClient:
    def __init__(self, media=b"IMG"):
        self.sent = []          # (kind, to, text, choices)
        self.media = media
        self.downloaded = []

    async def send_text(self, to, text):
        self.sent.append(("text", to, text, None))

    async def send_choices(self, to, text, choices):
        self.sent.append(("choices", to, text, list(choices)))

    async def download_media(self, media_ref):
        self.downloaded.append(media_ref)
        return self.media


def run(coro):
    import asyncio
    return asyncio.run(coro)


def text_event(channel, body, sender="555"):
    return IncomingEvent(channel=channel, sender_id=sender, kind=EventKind.TEXT, text=body)


class FlowTests(unittest.TestCase):
    CHANNELS = ("whatsapp", "telegram")

    def setUp(self):
        for target, value in (
            (factories, ("find_by_contact", mock.Mock(return_value=FACTORY))),
        ):
            p = mock.patch.object(target, value[0], value[1])
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(factory_flow, "is_import_command", mock.Mock(return_value=False))
        self.is_import = p.start()
        self.addCleanup(p.stop)

    def handle(self, event, client=None):
        client = client or FakeClient()
        run(factory_flow.handle_event(object(), event, client))
        return client

    def test_unregistered_sender_ignored(self):
        for ch in self.CHANNELS:
            with self.subTest(ch=ch), mock.patch.object(factories, "find_by_contact", return_value=None):
                self.assertEqual(self.handle(text_event(ch, "سلام")).sent, [])

    def test_factory_lookup_uses_event_channel(self):
        self.handle(text_event("telegram", "سلام", sender="42"))
        factories.find_by_contact.assert_called_once()
        self.assertEqual(factories.find_by_contact.call_args.args[1:], ("telegram", "42"))

    def test_greeting_and_help_and_default(self):
        for ch in self.CHANNELS:
            with self.subTest(ch=ch):
                self.assertEqual(self.handle(text_event(ch, "السلام عليكم")).sent[0][2], texts.greeting(FACTORY))
                self.assertEqual(self.handle(text_event(ch, "اضافة منتج")).sent[0][2], texts.add_product_help(FACTORY))
                self.assertEqual(self.handle(text_event(ch, "بلا معنى")).sent[0][2], texts.GENERIC_WELCOME)

    def test_reply_goes_to_sender(self):
        c = self.handle(text_event("telegram", "سلام", sender="42"))
        self.assertEqual(c.sent[0][1], "42")

    def test_bad_caption_rejected(self):
        for ch in self.CHANNELS:
            with self.subTest(ch=ch):
                ev = IncomingEvent(channel=ch, sender_id="1", kind=EventKind.IMAGE, media_ref="M", caption="كلام")
                with mock.patch.object(product_intake, "prepare_submission",
                                       return_value=product_intake.Rejected(product_intake.RejectionCode.BAD_CAPTION)):
                    c = self.handle(ev)
                self.assertEqual(c.sent[0][2], texts.CAPTION_FORMAT_ERROR)
                self.assertEqual(c.downloaded, [])

    def test_existing_product_asks_before_writing(self):
        sub = product_intake.Submission("B001", "حجر", 100.0, "M", exists=True)
        ev = IncomingEvent(channel="telegram", sender_id="1", kind=EventKind.IMAGE, media_ref="M", caption="c")
        with mock.patch.object(product_intake, "prepare_submission", return_value=sub), \
             mock.patch.object(product_intake, "request_update_confirmation",
                               return_value=SimpleNamespace(id=7)) as req, \
             mock.patch.object(product_intake, "store_product") as store:
            c = self.handle(ev)
        req.assert_called_once()
        self.assertEqual(req.call_args.args[2], "telegram")      # الطلب مرتبط بقناته
        store.assert_not_called()
        self.assertEqual(c.downloaded, [])                       # لا تنزيل قبل الموافقة
        kind, _, _, choices = c.sent[0]
        self.assertEqual(kind, "choices")
        self.assertEqual([i for i, _ in choices], ["upd:yes:7", "upd:no:7"])

    def test_new_product_downloads_stores_and_confirms(self):
        sub = product_intake.Submission("B001", "حجر", 100.0, "M1", exists=False)
        product = SimpleNamespace(sku="B001", title="حجر", price=100.0, meta_product_id="9")
        result = product_intake.StoreResult(product_intake.StoreStatus.STORED, product, created=True, synced=True)

        async def fake_store(db, factory, submission, content):
            self.assertEqual((submission, content), (sub, b"IMG"))
            return result

        ev = IncomingEvent(channel="whatsapp", sender_id="1", kind=EventKind.IMAGE, media_ref="M1", caption="c")
        with mock.patch.object(product_intake, "prepare_submission", return_value=sub), \
             mock.patch.object(product_intake, "store_product", fake_store):
            c = self.handle(ev)
        self.assertEqual(c.downloaded, ["M1"])
        self.assertEqual(c.sent[0][2], texts.stored(product, True, True))

    def test_store_failure_reported(self):
        sub = product_intake.Submission("B001", "حجر", 100.0, "M1", exists=False)
        failure = product_intake.StoreResult(product_intake.StoreStatus.NOT_IMAGE)

        async def fake_store(*a):
            return failure

        ev = IncomingEvent(channel="whatsapp", sender_id="1", kind=EventKind.IMAGE, media_ref="M1", caption="c")
        with mock.patch.object(product_intake, "prepare_submission", return_value=sub), \
             mock.patch.object(product_intake, "store_product", fake_store):
            c = self.handle(ev)
        self.assertEqual(c.sent[0][2], texts.store_failure(product_intake.StoreStatus.NOT_IMAGE, "B001"))

    def test_choice_no_cancels_without_download(self):
        pending = SimpleNamespace(id=7)
        sub = product_intake.Submission("B001", "حجر", 100.0, "M", exists=True)
        ev = IncomingEvent(channel="telegram", sender_id="1", kind=EventKind.CHOICE, choice_id="upd:no:7")
        with mock.patch.object(product_intake, "find_pending", return_value=pending) as find, \
             mock.patch.object(product_intake, "consume_pending", return_value=sub):
            c = self.handle(ev)
        self.assertEqual(find.call_args.args[2], "telegram")
        self.assertEqual(find.call_args.kwargs["pending_id"], 7)
        self.assertEqual(c.sent[0][2], texts.update_cancelled("B001"))
        self.assertEqual(c.downloaded, [])

    def test_choice_expired(self):
        ev = IncomingEvent(channel="whatsapp", sender_id="1", kind=EventKind.CHOICE, choice_id="upd:yes:7")
        with mock.patch.object(product_intake, "find_pending", return_value=None):
            c = self.handle(ev)
        self.assertEqual(c.sent[0][2], texts.PENDING_EXPIRED)

    def test_unknown_choice_id_ignored(self):
        ev = IncomingEvent(channel="whatsapp", sender_id="1", kind=EventKind.CHOICE, choice_id="other")
        self.assertEqual(self.handle(ev).sent, [])

    def test_text_yes_resolves_latest_pending(self):
        pending = SimpleNamespace(id=3)
        sub = product_intake.Submission("B002", "حجر", 5.0, "M", exists=True)
        with mock.patch.object(product_intake, "find_pending", return_value=pending), \
             mock.patch.object(product_intake, "consume_pending", return_value=sub) as consume:
            c = self.handle(text_event("telegram", "لا"))
        consume.assert_called_once()
        self.assertEqual(c.sent[0][2], texts.update_cancelled("B002"))

    def test_text_yes_without_pending_falls_back_to_welcome(self):
        with mock.patch.object(product_intake, "find_pending", return_value=None):
            c = self.handle(text_event("whatsapp", "نعم"))
        self.assertEqual(c.sent[0][2], texts.GENERIC_WELCOME)


class ImportCommandFlowTests(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(factory_flow, "is_import_command", mock.Mock(return_value=True))
        p.start()
        self.addCleanup(p.stop)
        # أمر المدير لا يحتاج مصنعاً مسجلاً
        p = mock.patch.object(factories, "find_by_contact", mock.Mock(side_effect=AssertionError("unexpected")))
        p.start()
        self.addCleanup(p.stop)

    def go(self, job, channel="telegram"):
        client = FakeClient()
        with mock.patch.object(factory_flow, "run_import_job", job):
            run(factory_flow.handle_event(object(), text_event(channel, "pw"), client))
        return [m[2] for m in client.sent]

    def test_busy(self):
        async def job(**kw):
            raise ImportBusy()
        self.assertEqual(self.go(job), [texts.IMPORT_BUSY])

    def test_config_error(self):
        async def job(**kw):
            raise ImportConfigError("ملف ناقص")
        self.assertEqual(self.go(job), ["❌ ملف ناقص"])

    def test_unexpected_error_hides_details(self):
        async def job(**kw):
            raise RuntimeError("secret detail")
        self.assertEqual(self.go(job), [texts.IMPORT_FAILED])

    def test_success_sends_progress_then_summary(self):
        report = ImportReport(csv_path="x.csv", touched_ids=[1, 2, 3], applied=True)

        async def job(*, on_started, on_written):
            await on_started()
            await on_written(3)
            return ImportJobResult(report, sync_failed=1)

        msgs = self.go(job)
        self.assertEqual(msgs[0], texts.IMPORT_STARTED)
        self.assertEqual(msgs[1], texts.import_written(3))
        self.assertIn("نجحت 2، فشلت 1", msgs[2])


if __name__ == "__main__":
    unittest.main()
