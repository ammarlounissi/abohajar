import unittest

from app.channels.base import EventKind
from app.channels.whatsapp.adapter import to_event


class ToEventTests(unittest.TestCase):
    def test_text(self):
        e = to_event({"id": "w1", "from": "213550000000", "type": "text", "text": {"body": "سلام"}})
        self.assertEqual((e.channel, e.sender_id, e.kind, e.text), ("whatsapp", "213550000000", EventKind.TEXT, "سلام"))

    def test_image_with_caption(self):
        e = to_event({"from": "1", "type": "image", "image": {"id": "MEDIA1", "caption": "حجر - 100 - B001"}})
        self.assertEqual((e.kind, e.media_ref, e.caption), (EventKind.IMAGE, "MEDIA1", "حجر - 100 - B001"))

    def test_image_without_caption(self):
        e = to_event({"from": "1", "type": "image", "image": {"id": "M"}})
        self.assertEqual(e.caption, "")

    def test_button_reply(self):
        e = to_event({"from": "1", "type": "interactive",
                      "interactive": {"type": "button_reply", "button_reply": {"id": "upd:yes:7", "title": "x"}}})
        self.assertEqual((e.kind, e.choice_id), (EventKind.CHOICE, "upd:yes:7"))

    def test_unsupported_or_malformed(self):
        self.assertIsNone(to_event({"from": "1", "type": "audio"}))
        self.assertIsNone(to_event({"type": "text", "text": {"body": "x"}}))
        self.assertIsNone(to_event({}))

    def test_text_body_missing_is_empty(self):
        self.assertEqual(to_event({"from": "1", "type": "text", "text": None}).text, "")


if __name__ == "__main__":
    unittest.main()
