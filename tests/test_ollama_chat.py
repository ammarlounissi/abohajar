import json
import unittest
from unittest import mock

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.config import settings
from app.core.database import Base, get_db
from app.routers import chat as chat_router
from app.services import ollama_chat


class OllamaChatRouterTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add(models.Factory(
                id=1, name="مصنع الاختبار", activity="صناعة الحجر الطبيعي", phone_number="213550000000",
            ))
            db.commit()

        app = FastAPI()
        app.include_router(chat_router.router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)
        key_patch = mock.patch.object(settings, "ADMIN_API_KEY", "test-admin-key")
        key_patch.start()
        self.addCleanup(key_patch.stop)
        self.addCleanup(engine.dispose)

    def request(self, prompt="أهلاً", factory_id=1, key="test-admin-key"):
        headers = {"X-API-Key": key} if key is not None else {}
        return self.client.post(
            "/api/chat/",
            json={"factory_id": factory_id, "prompt": prompt},
            headers=headers,
        )

    def test_requires_admin_api_key(self):
        self.assertEqual(self.request(key=None).status_code, 401)
        self.assertEqual(self.request(key="wrong").status_code, 401)

    def test_generates_and_persists_exchange(self):
        with mock.patch(
            "app.services.ollama_chat.generate_reply",
            new_callable=mock.AsyncMock,
            return_value="مرحباً بك",
        ) as generate:
            response = self.request()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            (body["factory_id"], body["prompt"], body["response"]),
            (1, "أهلاً", "مرحباً بك"),
        )
        self.assertIn("id", body)
        self.assertIn("created_at", body)
        generate.assert_awaited_once_with([
            {
                "role": "system",
                "content": (
                    f"{ollama_chat.SYSTEM_INSTRUCTION}\n\n"
                    "بيانات المصنع:\nالاسم: مصنع الاختبار\nالنشاط: صناعة الحجر الطبيعي"
                ),
            },
            {"role": "user", "content": "أهلاً"},
        ])
        with self.Session() as db:
            saved = db.query(models.FactoryChatMessage).one()
            self.assertEqual((saved.factory_id, saved.prompt, saved.response), (1, "أهلاً", "مرحباً بك"))

    def test_factory_history_is_sent_as_context_and_extended(self):
        with mock.patch(
            "app.services.ollama_chat.generate_reply",
            new_callable=mock.AsyncMock,
            side_effect=["الرد الأول", "الرد الثاني"],
        ) as generate:
            first = self.request(prompt="ما اسم المنتج؟")
            second = self.request(prompt="وما سعره؟")

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        first_messages = generate.await_args_list[0].args[0]
        second_messages = generate.await_args_list[1].args[0]
        self.assertEqual(
            [(message["role"], message["content"]) for message in first_messages[1:]],
            [("user", "ما اسم المنتج؟")],
        )
        self.assertEqual(
            [(message["role"], message["content"]) for message in second_messages[1:]],
            [
                ("user", "ما اسم المنتج؟"),
                ("assistant", "الرد الأول"),
                ("user", "وما سعره؟"),
            ],
        )
        with self.Session() as db:
            self.assertEqual(db.query(models.FactoryChatMessage).count(), 2)

    def test_missing_factory_is_not_found(self):
        with mock.patch("app.services.ollama_chat.generate_reply", new_callable=mock.AsyncMock) as generate:
            response = self.request(factory_id=404)
        self.assertEqual(response.status_code, 404)
        generate.assert_not_awaited()

    def test_invalid_prompt_is_rejected(self):
        self.assertEqual(self.request(prompt="   ").status_code, 422)

    def test_ollama_failure_is_reported_without_saving(self):
        with mock.patch(
            "app.services.ollama_chat.generate_reply",
            new_callable=mock.AsyncMock,
            side_effect=ollama_chat.OllamaUnavailableError,
        ):
            response = self.request()
        self.assertEqual(response.status_code, 502)
        with self.Session() as db:
            self.assertEqual(db.query(models.FactoryChatMessage).count(), 0)

    def test_context_keeps_only_latest_five_turns_and_fixed_profile(self):
        with self.Session() as db:
            db.add_all([
                models.FactoryChatMessage(factory_id=1, prompt=f"سؤال {i}", response=f"رد {i}")
                for i in range(7)
            ])
            db.commit()

        with mock.patch(
            "app.services.ollama_chat.generate_reply",
            new_callable=mock.AsyncMock,
            return_value="تم",
        ) as generate:
            response = self.request(prompt="السؤال الجديد")

        self.assertEqual(response.status_code, 200)
        messages = generate.await_args.args[0]
        self.assertEqual(len(messages), 12)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("مصنع الاختبار", messages[0]["content"])
        self.assertIn("صناعة الحجر الطبيعي", messages[0]["content"])
        self.assertEqual(
            [message["content"] for message in messages[1:]],
            [
                "سؤال 2", "رد 2", "سؤال 3", "رد 3", "سؤال 4",
                "رد 4", "سؤال 5", "رد 5", "سؤال 6", "رد 6", "السؤال الجديد",
            ],
        )


class OllamaServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_reply_calls_ollama_chat_endpoint(self):
        captured = {}

        def respond(request):
            captured["url"] = str(request.url)
            captured["payload"] = request.read()
            return httpx.Response(200, json={"message": {"role": "assistant", "content": "نص مولّد"}})

        transport = httpx.MockTransport(respond)
        client = httpx.AsyncClient(transport=transport)
        with (
            mock.patch("app.services.ollama_chat.make_client", return_value=client),
            mock.patch.object(settings, "OLLAMA_BASE_URL", "http://localhost:11434"),
            mock.patch.object(settings, "OLLAMA_MODEL", "qwen2.5:3b"),
        ):
            result = await ollama_chat.generate_reply([
                {"role": "system", "content": "تعليمات"},
                {"role": "user", "content": "اكتب وصفاً"},
            ])

        self.assertEqual(result, "نص مولّد")
        self.assertEqual(captured["url"], "http://localhost:11434/api/chat")
        self.assertEqual(
            json.loads(captured["payload"]),
            {
                "model": "qwen2.5:3b",
                "messages": [
                    {"role": "system", "content": "تعليمات"},
                    {"role": "user", "content": "اكتب وصفاً"},
                ],
                "stream": False,
            },
        )


if __name__ == "__main__":
    unittest.main()
