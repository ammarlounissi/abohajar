import asyncio
import json
import unittest
from unittest import mock
from urllib.parse import parse_qs

import httpx

from app.channels.messenger.adapter import _comment_event, _messenger_event
from app.channels.messenger.client import MessengerClient
from app.core.config import settings
from app import models
from app.core.database import Base
from app.services.social_channels import get_active_facebook_page
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class MetaPayloadTests(unittest.TestCase):
    def test_comment_payload(self):
        event = _comment_event({
            "field": "feed",
            "value": {
                "item": "comment",
                "comment_id": "c1",
                "post_id": "page_post",
                "message": "شحال؟",
                "from": {"id": "customer1"},
            },
        })
        self.assertEqual(event["comment_id"], "c1")
        self.assertEqual(event["post_id"], "page_post")
        self.assertEqual(event["text"], "شحال؟")

    def test_messenger_payload(self):
        event = _messenger_event({
            "sender": {"id": "customer1"},
            "recipient": {"id": "page1"},
            "message": {"mid": "mid.1", "text": "B001"},
        }, {"id": "page1"})
        self.assertEqual(
            (event["message_id"], event["sender_id"], event["text"]),
            ("mid.1", "customer1", "B001"),
        )


class MessengerClientTests(unittest.TestCase):
    def test_sends_private_reply_and_message(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"id": "ok"})

        async def run():
            client = MessengerClient(httpx.MockTransport(handler))
            await client.send_private_reply("c1", "رد")
            await client.send_message("customer1", "مرحبًا")

        with mock.patch.object(settings, "META_PAGE_ACCESS_TOKEN", "PAGE_TOKEN"), \
                mock.patch.object(settings, "META_PAGE_ID", "page1"):
            asyncio.run(run())

        self.assertEqual(requests[0].url.path.rsplit("/", 1)[-1], "private_replies")
        self.assertEqual(parse_qs(requests[0].content.decode())["access_token"], ["PAGE_TOKEN"])
        fields = parse_qs(requests[1].content.decode())
        self.assertEqual(json.loads(fields["recipient"][0]), {"id": "customer1"})
        self.assertEqual(json.loads(fields["message"][0]), {"text": "مرحبًا"})


class ActivePageTests(unittest.TestCase):
    def test_only_registered_active_facebook_page_is_eligible(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as db:
            db.add(models.Factory(id=1, name="مصنع", phone_number="213550000000"))
            db.add_all([
                models.SocialChannel(id=1, factory_id=1, platform="facebook", name="نشطة", remote_id="111111"),
                models.SocialChannel(id=2, factory_id=1, platform="facebook", name="البوت متوقف", remote_id="222222", is_active=True, bot_enabled=False),
            ])
            db.commit()
            self.assertIsNotNone(get_active_facebook_page(db, "111111"))
            self.assertIsNone(get_active_facebook_page(db, "222222"))
            self.assertIsNone(get_active_facebook_page(db, "333333"))


if __name__ == "__main__":
    unittest.main()
