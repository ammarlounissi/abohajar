"""نص منشور الزبون بـ Gemini: نشاط الصفحة (من فيسبوك أو يكتبه الزبون) وطابور الأوصاف الموجود."""
from unittest import mock

import httpx

from app import models
from app.services import customer_captions, ollama_chat
from tests.test_customer_workspace import BASE, WorkspaceTestBase


class CustomerCaptionTests(WorkspaceTestBase):
    def setUp(self):
        super().setUp()
        self.facebook_activity = ""
        self.facebook_calls = []

        def fake_fetch(page_id, token):
            self.facebook_calls.append((page_id, token))
            return self.facebook_activity

        patcher = mock.patch.object(customer_captions, "fetch_page_activity", side_effect=fake_fetch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_worker(self, reply="وصف جاهز للنشر", error=None):
        """ما يفعله عامل hadjret-description-worker، على قاعدة الاختبار وبنموذج مقلَّد."""
        side_effect = error or None

        async def fake_reply(messages, model, provider=None):
            self.sent = (messages, model, provider)
            if side_effect:
                raise side_effect
            return reply

        with mock.patch.object(ollama_chat, "generate_reply", side_effect=fake_reply):
            job_id = ollama_chat.claim_next_description_job(self.Session)
            self.assertIsNotNone(job_id)
            ollama_chat.process_description_job(job_id, self.Session)
        return job_id

    # ───── الوصول ─────

    def test_requires_login_and_a_linked_page(self):
        for method, path in (("get", "/profile"), ("post", "/captions"), ("get", "/captions/1")):
            self.assertEqual(getattr(self.client(), method)(f"{BASE}{path}").status_code, 401)
            self.assertEqual(getattr(self.client(1), method)(f"{BASE}{path}").status_code, 409)

    # ───── النشاط ─────

    def test_empty_activity_is_filled_from_facebook_and_saved(self):
        self.link(1, page_id="111", name="مخبزة النور")
        self.facebook_activity = "مخبزة: خبز تقليدي وحلويات"
        body = self.client(1).get(f"{BASE}/profile").json()
        self.assertEqual(body, {
            "name": "مخبزة النور", "admin_managed": False, "activity": "مخبزة: خبز تقليدي وحلويات",
            "whatsapp": None, "hashtags": [],
        })
        self.assertEqual(self.facebook_calls, [("111", "tok")])
        self.client(1).get(f"{BASE}/profile")
        self.assertEqual(len(self.facebook_calls), 1)       # محفوظ: لا يُطلب فيسبوك مرة ثانية
        with self.Session() as db:
            self.assertEqual(db.get(models.Factory, self.factory_of(1)).activity, "مخبزة: خبز تقليدي وحلويات")

    def test_activity_stays_empty_when_facebook_gives_nothing(self):
        self.link(1)
        self.assertEqual(self.client(1).get(f"{BASE}/profile").json()["activity"], "")

    def test_customer_edits_activity_and_facebook_is_not_asked_again(self):
        self.link(1)
        res = self.client(1).patch(f"{BASE}/profile", json={"activity": "  بيع   ملابس أطفال  "})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["activity"], "بيع ملابس أطفال")
        self.assertEqual(self.client(1).get(f"{BASE}/profile").json()["activity"], "بيع ملابس أطفال")
        self.assertEqual(self.facebook_calls, [])

    def test_too_short_activity_is_rejected(self):
        self.link(1)
        self.assertEqual(self.client(1).patch(f"{BASE}/profile", json={"activity": " ب "}).status_code, 422)

    def test_profile_belongs_to_the_users_own_page(self):
        self.link(1, page_id="111")
        self.link(2, page_id="222")
        self.client(1).patch(f"{BASE}/profile", json={"activity": "نشاط الأول"})
        self.assertEqual(self.client(2).get(f"{BASE}/profile").json()["activity"], "")

    def test_partial_update_changes_only_what_was_sent(self):
        self.link(1, name="مخبزة النور")
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"activity": "خبز تقليدي"})
        res = client.patch(f"{BASE}/profile", json={"whatsapp": "0550 12-34-56"})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(
            (res.json()["name"], res.json()["activity"], res.json()["whatsapp"]),
            ("مخبزة النور", "خبز تقليدي", "+213550123456"),
        )
        res = client.patch(f"{BASE}/profile", json={"name": "  مخبزة   النور الجديدة "})
        self.assertEqual((res.json()["name"], res.json()["whatsapp"]), ("مخبزة النور الجديدة", "+213550123456"))

    def test_whatsapp_formats_and_clearing(self):
        self.link(1)
        client = self.client(1)
        for given, saved in (("+213 550 12 34 56", "+213550123456"), ("00213550123456", "+213550123456"),
                             ("213660000000", "+213660000000"), ("0770000000", "+213770000000")):
            self.assertEqual(client.patch(f"{BASE}/profile", json={"whatsapp": given}).json()["whatsapp"], saved)
        self.assertIsNone(client.patch(f"{BASE}/profile", json={"whatsapp": ""}).json()["whatsapp"])
        client.patch(f"{BASE}/profile", json={"whatsapp": "0550123456"})
        self.assertIsNone(client.patch(f"{BASE}/profile", json={"whatsapp": None}).json()["whatsapp"])
        for bad in ("abc", "123", "+2135501234567890"):
            self.assertEqual(client.patch(f"{BASE}/profile", json={"whatsapp": bad}).status_code, 422, bad)

    def test_whatsapp_and_hashtags_belong_to_the_page_factory_not_the_user(self):
        self.link(1)
        self.client(1).patch(f"{BASE}/profile", json={"whatsapp": "0550123456", "hashtags": ["مخبزة"]})
        with self.Session() as db:
            factory = db.get(models.Factory, self.factory_of(1))
            self.assertEqual((factory.phone_number, factory.hashtags), ("+213550123456", ["#مخبزة"]))
            # جهة اتصال واتساب تتبع الرقم كما في مسار الإدارة (factories.update_factory)
            self.assertEqual(
                [(c.channel, c.identifier) for c in factory.contacts], [("whatsapp", "213550123456")],
            )
            columns = models.CustomerWorkspace.__table__.columns.keys()
            self.assertNotIn("whatsapp", columns)
            self.assertNotIn("hashtags", columns)

    def test_clearing_the_number_removes_its_whatsapp_contact(self):
        self.link(1)
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"whatsapp": "0550123456"})
        client.patch(f"{BASE}/profile", json={"whatsapp": "0660000000"})
        with self.Session() as db:
            factory = db.get(models.Factory, self.factory_of(1))
            self.assertEqual([c.identifier for c in factory.contacts], ["213660000000"])
        self.assertIsNone(client.patch(f"{BASE}/profile", json={"whatsapp": ""}).json()["whatsapp"])
        with self.Session() as db:
            factory = db.get(models.Factory, self.factory_of(1))
            self.assertEqual((factory.phone_number, list(factory.contacts)), (None, []))
            self.assertEqual(db.query(models.FactoryContact).count(), 0)

    def test_a_number_registered_to_another_factory_is_refused(self):
        with self.Session() as db:
            db.add(models.Factory(name="مصنع آخر", phone_number="+213550123456"))
            db.commit()
        self.link(1)
        res = self.client(1).patch(f"{BASE}/profile", json={"whatsapp": "0550123456", "activity": "خبز تقليدي"})
        self.assertEqual(res.status_code, 422)
        self.assertIn("مسجّل", res.json()["detail"])
        body = self.client(1).get(f"{BASE}/profile").json()
        self.assertEqual((body["whatsapp"], body["activity"]), (None, ""))      # لا يُحفظ شيء من الطلب

    def test_hashtags_are_normalized_deduplicated_and_limited(self):
        self.link(1)
        client = self.client(1)
        res = client.patch(f"{BASE}/profile", json={"hashtags": ["مخبزة", "#وهران", "خبز تقليدي", "#مخبزة", " "]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["hashtags"], ["#مخبزة", "#وهران", "#خبز_تقليدي"])
        self.assertEqual(client.patch(f"{BASE}/profile", json={"hashtags": []}).json()["hashtags"], [])
        self.assertEqual(client.patch(f"{BASE}/profile", json={"hashtags": ["خبز!"]}).status_code, 422)
        many = [f"وسم{i}" for i in range(11)]
        self.assertEqual(client.patch(f"{BASE}/profile", json={"hashtags": many}).status_code, 422)

    def test_supplier_page_name_and_number_are_managed_by_admin(self):
        with self.Session() as db:
            db.add(models.Factory(id=7, name="مصنع الحجر", phone_number="213550000000", activity="حجر طبيعي"))
            db.add(models.SocialChannel(
                platform="facebook", name="صفحة المصنع", remote_id="111", page_id="111", factory_id=7,
            ))
            db.commit()
        self.link(1)
        client = self.client(1)
        profile = client.get(f"{BASE}/profile").json()
        self.assertEqual((profile["admin_managed"], profile["whatsapp"]), (True, "213550000000"))
        res = client.patch(f"{BASE}/profile", json={"name": "اسم آخر"})
        self.assertEqual(res.status_code, 422)
        self.assertIn("الإدارة", res.json()["detail"])
        res = client.patch(f"{BASE}/profile", json={"whatsapp": "0660000000"})
        self.assertEqual(res.status_code, 422)
        self.assertIn("الإدارة", res.json()["detail"])
        res = client.patch(f"{BASE}/profile", json={"name": "مصنع الحجر", "hashtags": ["حجر"]})
        self.assertEqual(res.status_code, 200, res.text)   # الاسم نفسه دون تغيير مقبول، والوسوم له
        with self.Session() as db:
            factory = db.get(models.Factory, 7)
            self.assertEqual((factory.name, factory.phone_number, factory.hashtags), ("مصنع الحجر", "213550000000", ["#حجر"]))

    # ───── كتابة الوصف ─────

    def test_caption_is_queued_then_written_by_the_worker(self):
        self.link(1, name="مخبزة النور")
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"activity": "خبز تقليدي وحلويات"})
        res = client.post(f"{BASE}/captions")
        self.assertEqual(res.status_code, 202, res.text)
        job = res.json()
        self.assertEqual((job["status"], job["caption"], job["error"]), ("queued", None, None))
        self.assertNotIn("factory_id", job)

        self.run_worker(reply="  خبز ساخن كل صباح  ")
        done = client.get(f"{BASE}/captions/{job['id']}").json()
        self.assertEqual((done["status"], done["caption"]), ("succeeded", "خبز ساخن كل صباح"))
        self.assertIsNotNone(done["completed_at"])

        messages, model, provider = self.sent
        self.assertEqual((model, provider), (customer_captions.CAPTION_MODEL, "gemini"))
        self.assertEqual(messages[1]["content"], "خبز تقليدي وحلويات")
        self.assertIn("مخبزة النور", messages[0]["content"])
        self.assertNotIn(ollama_chat.STONE_TERMINOLOGY, messages[0]["content"])   # مصنع زبون: تعليمات عامة
        self.assertNotIn("الحجر", messages[0]["content"])

    def test_whatsapp_and_hashtags_are_appended_after_the_model_text(self):
        self.link(1)
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={
            "activity": "خبز تقليدي", "whatsapp": "0550123456", "hashtags": ["مخبزة", "وهران"],
        })
        job_id = client.post(f"{BASE}/captions").json()["id"]
        self.run_worker(reply="خبز ساخن كل صباح #وهران")
        caption = client.get(f"{BASE}/captions/{job_id}").json()["caption"]
        self.assertEqual(caption, (
            "خبز ساخن كل صباح #وهران\n\n📲 للطلب أو الاستفسار عبر واتساب: +213550123456\n\n#مخبزة"
        ))
        self.assertNotIn("0550123456", self.sent[0][0]["content"])   # الرقم لا يمرّ بالنموذج
        with self.Session() as db:   # سجل «تجنّب» يحفظ نص النموذج وحده
            self.assertEqual(db.query(models.FactoryChatMessage).one().response, "خبز ساخن كل صباح #وهران")

    def test_without_saved_hashtags_the_model_suggests_them_on_a_last_line(self):
        self.link(1)
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"activity": "ملابس أطفال", "whatsapp": "0550123456"})
        job_id = client.post(f"{BASE}/captions").json()["id"]
        self.run_worker(reply="ملابس مريحة لأطفالكم بأسعار مناسبة.\n\n#ملابس_أطفال #موضة #أطفال #تخفيضات #جديد #زائد")
        self.assertIn(ollama_chat.HASHTAG_INSTRUCTION, self.sent[0][0]["content"])
        caption = client.get(f"{BASE}/captions/{job_id}").json()["caption"]
        self.assertEqual(caption, (
            "ملابس مريحة لأطفالكم بأسعار مناسبة."
            "\n\n📲 للطلب أو الاستفسار عبر واتساب: +213550123456"
            "\n\n#ملابس_أطفال #موضة #أطفال #تخفيضات #جديد"          # حتى 5، بعد سطر واتساب
        ))

    def test_saved_hashtags_replace_the_models_and_it_is_not_asked_for_them(self):
        self.link(1)
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"activity": "ملابس أطفال", "hashtags": ["متجر_الإخلاص"]})
        job_id = client.post(f"{BASE}/captions").json()["id"]
        self.run_worker(reply="ملابس مريحة لأطفالكم.\n#موضة #أطفال")
        self.assertNotIn(ollama_chat.HASHTAG_INSTRUCTION, self.sent[0][0]["content"])
        caption = client.get(f"{BASE}/captions/{job_id}").json()["caption"]
        self.assertEqual(caption, "ملابس مريحة لأطفالكم.\n\n#متجر_الإخلاص")

    def test_model_reply_without_a_hashtag_line_keeps_its_text(self):
        self.assertEqual(customer_captions.split_hashtags("نص عادي #وسم_داخلي فيه"), ("نص عادي #وسم_داخلي فيه", []))
        self.assertEqual(customer_captions.split_hashtags("نص\n#أ_ب #ج_د\n\n"), ("نص", ["#أ_ب", "#ج_د"]))
        self.assertEqual(customer_captions.split_hashtags("نص\n#أب\n#جد #أب"), ("نص", ["#أب", "#جد"]))

    def test_admin_descriptions_do_not_ask_for_hashtags(self):
        with self.Session() as db:
            factory = models.Factory(name="مصنع", phone_number="213550000009", activity="حجر طبيعي")
            db.add(factory)
            db.commit()
            job = ollama_chat.enqueue_description_job(db, factory.id, "gemini-3.1-flash-lite")
            self.assertNotIn(ollama_chat.HASHTAG_INSTRUCTION, job.messages[0]["content"])

    # ───── تنويع الجملة الأولى ─────

    def test_opening_helpers(self):
        self.assertEqual(ollama_chat.first_sentence("«متجر» يقدم الجديد! ثم كلام."), "«متجر» يقدم الجديد")
        self.assertEqual(ollama_chat.first_sentence("سطر أول\nسطر ثانٍ"), "سطر أول")
        self.assertEqual(ollama_chat.opening_key("  «سوبرماركت ملابس» الابطال يرحب"), "سوبرماركت ملابس")
        self.assertEqual(ollama_chat.opening_key("✨ هل تبحث، عن جديد؟"), "هل تبحث")

    def test_instructions_forbid_starting_with_the_name_or_kind(self):
        self.assertIn("لا تبدأ باسم النشاط", ollama_chat.SYSTEM_INSTRUCTION)
        self.assertIn("«سوبرماركت»", ollama_chat.SYSTEM_INSTRUCTION)

    def test_opening_style_rotates_between_consecutive_captions(self):
        self.link(1)
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"activity": "ملابس أطفال"})
        styles = []
        for reply in ("أول وصف هنا.", "ثاني وصف هنا.", "ثالث وصف هنا."):
            client.post(f"{BASE}/captions")
            self.run_worker(reply=reply)
            line = next(l for l in self.sent[0][0]["content"].splitlines() if "أسلوب الجملة الأولى" in l)
            styles.append(line)
        self.assertEqual(len(set(styles)), 3)
        self.assertEqual(styles[0], f"- أسلوب الجملة الأولى: {ollama_chat.VARIATION_OPENINGS[0]}")

    def test_previous_openings_and_first_words_are_named_as_forbidden(self):
        self.link(1)
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"activity": "ملابس أطفال"})
        client.post(f"{BASE}/captions")
        self.run_worker(reply="سوبرماركت ملابس الابطال يرحب بكم. تسوقوا الآن.\n#ملابس")
        client.post(f"{BASE}/captions")
        self.run_worker(reply="هل تبحث عن الجديد؟ تعال.")
        system = self.sent[0][0]["content"]
        self.assertIn("  - سوبرماركت ملابس الابطال يرحب بكم", system)
        self.assertIn("ممنوع أن يبدأ الوصف بأي من: «سوبرماركت ملابس»", system)

    def test_repeated_opening_is_rewritten_once(self):
        self.link(1)
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"activity": "ملابس أطفال"})
        client.post(f"{BASE}/captions")
        self.run_worker(reply="سوبرماركت ملابس الابطال يرحب بكم.")
        job_id = client.post(f"{BASE}/captions").json()["id"]
        calls = []

        async def fake_reply(messages, model, provider=None):
            calls.append(messages)
            return "سوبرماركت ملابس الابطال يقدم الجديد." if len(calls) == 1 else "ألوان زاهية تنتظر صغاركم."

        with mock.patch.object(ollama_chat, "generate_reply", side_effect=fake_reply):
            ollama_chat.process_description_job(ollama_chat.claim_next_description_job(self.Session), self.Session)
        self.assertEqual(len(calls), 2)
        self.assertEqual([m["role"] for m in calls[1][-2:]], ["assistant", "user"])
        self.assertIn("«سوبرماركت ملابس»", calls[1][-1]["content"])
        self.assertEqual(client.get(f"{BASE}/captions/{job_id}").json()["caption"], "ألوان زاهية تنتظر صغاركم.")

    def test_new_opening_is_not_rewritten_and_a_failed_rewrite_keeps_the_first(self):
        self.link(1)
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"activity": "ملابس أطفال"})
        client.post(f"{BASE}/captions")
        self.run_worker(reply="سوبرماركت ملابس الابطال يرحب بكم.")
        client.post(f"{BASE}/captions")
        self.run_worker(reply="ألوان زاهية تنتظر صغاركم.")      # بداية جديدة: نداء واحد
        job_id = client.post(f"{BASE}/captions").json()["id"]
        calls = []

        async def fake_reply(messages, model, provider=None):
            calls.append(messages)
            if len(calls) == 2:
                raise ollama_chat.OllamaUnavailableError("down", retryable=False)
            return "ألوان زاهية من جديد."

        with mock.patch.object(ollama_chat, "generate_reply", side_effect=fake_reply):
            ollama_chat.process_description_job(ollama_chat.claim_next_description_job(self.Session), self.Session)
        body = client.get(f"{BASE}/captions/{job_id}").json()
        self.assertEqual((body["status"], body["caption"]), ("succeeded", "ألوان زاهية من جديد."))

    def test_instructions_cap_the_caption_at_thirty_words(self):
        self.assertEqual(ollama_chat.MAX_DESCRIPTION_WORDS, 30)
        self.assertIn("30 كلمة", ollama_chat.SYSTEM_INSTRUCTION)

    def test_previous_captions_are_sent_as_an_avoid_list(self):
        self.link(1)
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"activity": "خبز تقليدي"})
        client.post(f"{BASE}/captions")
        self.run_worker(reply="الوصف الأول للمخبزة")
        client.post(f"{BASE}/captions")
        self.run_worker(reply="الوصف الثاني")
        self.assertIn("الوصف الأول للمخبزة", self.sent[0][0]["content"])

    def test_double_click_returns_the_caption_already_in_progress(self):
        self.link(1)
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"activity": "خبز تقليدي"})
        first = client.post(f"{BASE}/captions").json()
        second = client.post(f"{BASE}/captions").json()
        self.assertEqual(first["id"], second["id"])
        with self.Session() as db:
            self.assertEqual(db.query(models.FactoryDescriptionJob).count(), 1)

    def test_caption_uses_facebook_activity_when_none_was_written(self):
        self.link(1)
        self.facebook_activity = "متجر: عطور"
        self.assertEqual(self.client(1).post(f"{BASE}/captions").status_code, 202)
        with self.Session() as db:
            self.assertEqual(db.query(models.FactoryDescriptionJob).one().prompt, "متجر: عطور")

    def test_caption_without_any_activity_asks_the_customer_to_write_it(self):
        self.link(1)
        res = self.client(1).post(f"{BASE}/captions")
        self.assertEqual(res.status_code, 422)
        self.assertIn("نشاط صفحتك", res.json()["detail"])

    def test_failed_caption_shows_a_friendly_error_and_the_technical_detail(self):
        self.link(1)
        client = self.client(1)
        client.patch(f"{BASE}/profile", json={"activity": "خبز تقليدي"})
        job_id = client.post(f"{BASE}/captions").json()["id"]
        self.run_worker(error=ollama_chat.OllamaUnavailableError("Gemini أعاد HTTP 503", retryable=False))
        body = client.get(f"{BASE}/captions/{job_id}").json()
        self.assertEqual((body["status"], body["caption"]), ("failed", None))
        self.assertEqual(body["error"], customer_captions.FRIENDLY_FAILURE)
        self.assertIn("503", body["error_detail"])
        self.assertEqual(client.post(f"{BASE}/captions").status_code, 202)   # تُعاد المحاولة بطلب جديد

    def test_caption_job_of_another_page_is_not_found(self):
        self.link(1, page_id="111")
        self.link(2, page_id="222")
        self.client(1).patch(f"{BASE}/profile", json={"activity": "خبز تقليدي"})
        job_id = self.client(1).post(f"{BASE}/captions").json()["id"]
        self.assertEqual(self.client(2).get(f"{BASE}/captions/{job_id}").status_code, 404)

    def test_supplier_factory_keeps_stone_terminology(self):
        with self.Session() as db:
            factory = models.Factory(name="مصنع الحجر", phone_number="213550000000", activity="حجر طبيعي")
            db.add(factory)
            db.commit()
            messages = ollama_chat._conversation_messages(factory, [], "حجر طبيعي")
        self.assertIn(ollama_chat.STONE_TERMINOLOGY, messages[0]["content"])


class FetchPageActivityTests(WorkspaceTestBase):
    def fetch(self, handler):
        return customer_captions.fetch_page_activity("111", "tok", transport=httpx.MockTransport(handler))

    def test_reads_category_and_about_with_the_page_token(self):
        seen = {}

        def handler(request):
            seen["auth"] = request.headers["Authorization"]
            seen["fields"] = request.url.params["fields"]
            seen["path"] = request.url.path
            return httpx.Response(200, json={"category": "مخبزة", "about": "خبز\n تقليدي", "id": "111"})

        self.assertEqual(self.fetch(handler), "مخبزة: خبز تقليدي")
        self.assertEqual((seen["auth"], seen["fields"]), ("Bearer tok", "category,about,description"))
        self.assertTrue(seen["path"].endswith("/111"))

    def test_falls_back_to_description_or_category_alone(self):
        self.assertEqual(self.fetch(lambda r: httpx.Response(200, json={"description": "عطور"})), "عطور")
        self.assertEqual(self.fetch(lambda r: httpx.Response(200, json={"category": "متجر"})), "متجر")

    def test_long_text_is_cut_to_the_activity_limit(self):
        text = self.fetch(lambda r: httpx.Response(200, json={"about": "ب" * 900}))
        self.assertEqual(len(text), customer_captions.MAX_ACTIVITY_CHARS)

    def test_any_failure_returns_empty(self):
        error = {"error": {"message": "expired", "code": 190}}
        self.assertEqual(self.fetch(lambda r: httpx.Response(400, json=error)), "")
        self.assertEqual(self.fetch(lambda r: httpx.Response(200, content=b"not json")), "")

        def down(request):
            raise httpx.ConnectError("down")

        self.assertEqual(self.fetch(down), "")
