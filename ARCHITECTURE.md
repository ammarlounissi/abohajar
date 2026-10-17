# بنية الخلفية: فصل المنطق عن القنوات

```
routers/        طبقة HTTP رقيقة: تحقق مدخلات + استدعاء خدمة + شكل الاستجابة
channels/       كل ما يخص التحدث مع مصنع عبر قناة محادثة
  base.py         العقد: IncomingEvent و ChannelClient
  factory_flow.py سير المحادثة (أوامر، أسئلة، ردود) — مشترك بين كل القنوات
  texts.py        نصوص الردود العربية
  whatsapp/       المحوّل (adapter.py) والعميل (client.py) فقط
services/       منطق العمل. لا يعرف HTTP ولا واتساب ولا تيليجرام
  errors.py       NotFound / Duplicate / Conflict / InvalidInput (تترجمها main.py إلى HTTP)
  products.py, factories.py, product_intake.py, media*.py, meta.py, import_job.py ...
models.py, schemas.py, core/
```

الاتجاه الوحيد المسموح: `routers, channels → services → models/core`.
يحرسه `tests/test_architecture.py`.

## قواعد

- الخدمات تُرجع **بيانات ورموز** (`Rejected`, `StoreResult`, `ImportJobResult`) لا نصوصاً. الصياغة في `channels/texts.py`.
- الخدمات لا تنزّل وسائط من قناة: القناة تنزّل البايتات وتمررها.
- `create_/update_/delete_` تنفّذ commit؛ `upsert_/import_csv` لا (المستدعي يكتب الملفات أولاً ثم يحفظ).
- هوية المصنع في أي قناة = `factory_contacts(channel, identifier)` عبر `services.factories.find_by_contact`.
  معرّف غير مسجَّل يُتجاهل. عمود `channel` نص عادي فلا ترحيل عند إضافة قناة.

## إضافة قناة جديدة (مثل تيليجرام)

1. `channels/telegram/client.py`: صنف ينفّذ `ChannelClient` (`send_text`, `send_choices`, `download_media`).
2. `channels/telegram/adapter.py`: دالة `to_event(update) -> IncomingEvent` (+ منع التكرار بـ `services.dedup.claim_message`)،
   ثم `await factory_flow.handle_event(db, event, client)`.
3. `routers/telegram.py`: يتحقق من `secret_token` ويرد 200 ويعالج في `BackgroundTasks`.
4. تسجيل المصنع: `POST /api/factories/{id}/contacts` بـ `{"channel": "telegram", "identifier": "<user id>"}`.
5. (اختياري) قيّد أمر المدير: `ADMIN_CONTACTS=telegram:<user id>`.

لا يلزم تعديل أي خدمة ولا `factory_flow.py` ولا قاعدة البيانات.

## إضافة ميزة جديدة (مثل تغيير السعر)

1. دالة في `services/` تُرجع نتيجة منظَّمة وترفع أخطاء `ServiceError`.
2. مسار HTTP رقيق يستدعيها (للوحة التحكم).
3. أمر/زر في `factory_flow.py` + نص في `texts.py` (تظهر الميزة في كل القنوات دفعة واحدة).

## الترحيل

`alembic upgrade head` (خذ نسخة من ملف القاعدة أولاً): ينشئ `factory_contacts` وينسخ إليه أرقام
`factories.phone_number` كجهات واتساب، ويضيف `pending_media_updates.channel` ويعيد تسمية `media_id` إلى `media_ref`.

## النشر الاجتماعي (services/social)

- `services/social/base.py`: العقد `Publisher` (`validate` بلا شبكة ثم `_publish`) و`PublishResult` وتصنيف أخطاء Graph.
- `services/social/facebook.py` و`instagram.py`: محوّل لكل منصة. منصة جديدة (تيليجرام لاحقاً) = ملف محوّل + سطر في `services/social/__init__.py:REGISTRY`.
- `services/post_publisher.py`: الحجز الذري، `post_deliveries` لكل منصة، التقارير، العالقون. يُرجع بيانات لا نصوصاً عربية.
- `app/jobs/`: نقاط دخول الطرفية/systemd فقط (`publish_due`, `posts`). `services/` لا تستورد منها (يحرسه `tests/test_architecture.py`).
