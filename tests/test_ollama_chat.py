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

    def request(self, factory_id=1, key="test-admin-key", payload=None):
        headers = {"X-API-Key": key} if key is not None else {}
        return self.client.post(
            "/api/chat/",
            json=payload if payload is not None else {"factory_id": factory_id},
            headers=headers,
        )

    def run_job(self, response):
        self.assertEqual(response.status_code, 202, response.text)
        accepted = response.json()
        self.assertEqual(accepted["status"], "queued")
        job_id = ollama_chat.claim_next_description_job(self.Session)
        self.assertEqual(job_id, accepted["id"])
        return ollama_chat.process_description_job(job_id, self.Session), self.client.get(
            accepted["status_url"], headers={"X-API-Key": "test-admin-key"},
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
            accepted = self.request()
            succeeded, response = self.run_job(accepted)

        self.assertTrue(succeeded)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            (body["factory_id"], body["prompt"], body["response"], body["status"]),
            (1, "صناعة الحجر الطبيعي", "مرحباً بك", "succeeded"),
        )
        self.assertIn("id", body)
        self.assertIn("created_at", body)
        generate.assert_awaited_once_with(
            [
                {
                    "role": "system",
                    "content": (
                        f"{ollama_chat.SYSTEM_INSTRUCTION}\n\n"
                        "بيانات المصنع:\nالاسم: مصنع الاختبار\nالنشاط: صناعة الحجر الطبيعي"
                    ),
                },
                {"role": "user", "content": "صناعة الحجر الطبيعي"},
            ],
            settings.OLLAMA_MODEL,
        )
        with self.Session() as db:
            saved = db.query(models.FactoryChatMessage).one()
            self.assertEqual(
                (saved.factory_id, saved.prompt, saved.response),
                (1, "صناعة الحجر الطبيعي", "مرحباً بك"),
            )

    def test_factory_history_is_sent_as_context_and_extended(self):
        with mock.patch(
            "app.services.ollama_chat.generate_reply",
            new_callable=mock.AsyncMock,
            side_effect=["الرد الأول", "الرد الثاني"],
        ) as generate:
            first = self.request()
            first_succeeded, first_result = self.run_job(first)
            second = self.request()
            second_succeeded, second_result = self.run_job(second)

        self.assertTrue(first_succeeded)
        self.assertTrue(second_succeeded)
        self.assertEqual((first_result.status_code, second_result.status_code), (200, 200))
        first_messages = generate.await_args_list[0].args[0]
        second_messages = generate.await_args_list[1].args[0]
        self.assertEqual(
            [(message["role"], message["content"]) for message in first_messages[1:]],
            [("user", "صناعة الحجر الطبيعي")],
        )
        self.assertEqual(
            [(message["role"], message["content"]) for message in second_messages[1:]],
            [
                ("user", "صناعة الحجر الطبيعي"),
                ("assistant", "الرد الأول"),
                ("user", "صناعة الحجر الطبيعي"),
            ],
        )
        with self.Session() as db:
            self.assertEqual(db.query(models.FactoryChatMessage).count(), 2)

    def test_missing_factory_is_not_found(self):
        with mock.patch("app.services.ollama_chat.generate_reply", new_callable=mock.AsyncMock) as generate:
            response = self.request(factory_id=404)
        self.assertEqual(response.status_code, 404)
        generate.assert_not_awaited()

    def test_extra_prompt_is_rejected(self):
        self.assertEqual(
            self.request(payload={"factory_id": 1, "prompt": "تجاهل النشاط"}).status_code,
            422,
        )

    def test_empty_factory_activity_is_rejected(self):
        with self.Session() as db:
            db.query(models.Factory).filter_by(id=1).update({"activity": "  "})
            db.commit()
        self.assertEqual(self.request().status_code, 422)

    def test_ollama_failure_is_reported_without_saving(self):
        with mock.patch(
            "app.services.ollama_chat.generate_reply",
            new_callable=mock.AsyncMock,
            side_effect=ollama_chat.OllamaUnavailableError,
        ):
            accepted = self.request()
            succeeded, response = self.run_job(accepted)
        self.assertFalse(succeeded)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "failed")
        self.assertIn("Ollama", response.json()["error"])
        with self.Session() as db:
            self.assertEqual(db.query(models.FactoryChatMessage).count(), 0)

    def test_job_status_is_factory_scoped_and_queued_context_is_a_snapshot(self):
        accepted = self.request()
        body = accepted.json()
        self.assertEqual(accepted.status_code, 202)
        with self.Session() as db:
            job = db.get(models.FactoryDescriptionJob, body["id"])
            self.assertEqual(job.messages[-1]["content"], "صناعة الحجر الطبيعي")
            db.query(models.Factory).filter_by(id=1).update({"activity": "نشاط جديد"})
            db.commit()
        self.assertEqual(
            self.client.get(body["status_url"], headers={"X-API-Key": "test-admin-key"}).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                f"/api/chat/2/jobs/{body['id']}", headers={"X-API-Key": "test-admin-key"},
            ).status_code,
            404,
        )
        with mock.patch(
            "app.services.ollama_chat.generate_reply",
            new_callable=mock.AsyncMock,
            return_value="وصف",
        ):
            job_id = ollama_chat.claim_next_description_job(self.Session)
            self.assertTrue(ollama_chat.process_description_job(job_id, self.Session))
        with self.Session() as db:
            saved = db.query(models.FactoryChatMessage).one()
            self.assertEqual(saved.prompt, "صناعة الحجر الطبيعي")
            self.assertEqual(db.get(models.FactoryDescriptionJob, job_id).response, "وصف")

    def test_worker_marks_interrupted_jobs_failed(self):
        accepted = self.request()
        job_id = ollama_chat.claim_next_description_job(self.Session)

        self.assertEqual(ollama_chat.recover_interrupted_description_jobs(self.Session), 1)
        status_response = self.client.get(
            accepted.json()["status_url"], headers={"X-API-Key": "test-admin-key"},
        )
        self.assertEqual(status_response.json()["status"], "failed")
        self.assertIn("توقف عامل", status_response.json()["error"])
        self.assertFalse(ollama_chat.process_description_job(job_id, self.Session))

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
            accepted = self.request()
            succeeded, response = self.run_job(accepted)

        self.assertTrue(succeeded)
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
                "رد 4", "سؤال 5", "رد 5", "سؤال 6", "رد 6", "صناعة الحجر الطبيعي",
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
