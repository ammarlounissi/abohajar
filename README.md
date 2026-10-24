# Hojrat Bladi API (حجرة بلادي)

الخلفية والبوتات لمتجر "حجرة بلادي": FastAPI + SQLite + كتالوج Meta + بوتات المصانع.
المرجع الكامل للمشروع: `hadjretbladi-project-brief.md` (في مشروع Claude). شرح الفصل بين الطبقات وكيفية إضافة قناة: `ARCHITECTURE.md`.

كل الأوامر تُنفَّذ **من جذر المشروع** (المجلد الذي فيه `alembic.ini`).

---

## 1. بنية المشروع

```
abohajar/
├── app/
│   ├── main.py              تطبيق FastAPI: CORS، ترجمة أخطاء الخدمات إلى HTTP، تسجيل المسارات، /health
│   ├── models.py            جداول SQLAlchemy (Factory, FactoryContact, Product, Post, Order, ...)
│   ├── schemas.py           مخططات Pydantic للمدخلات والمخرجات
│   ├── core/
│   │   ├── config.py        كل الإعدادات من .env (+ settings.validate() عند الإقلاع)
│   │   ├── database.py      engine وSessionLocal وget_db
│   │   └── security.py      require_admin (X-API-Key) والتحقق من توقيع webhook ميتا
│   ├── routers/             طبقة HTTP رقيقة: تحقق مدخلات ← استدعاء خدمة ← استجابة
│   │   ├── products.py        /api/products (القراءة العامة + الإدارة + مزامنة ميتا + رفع CSV)
│   │   ├── factories.py       /api/factories وجهات اتصالها /contacts
│   │   ├── posts.py           /api/posts
│   │   ├── social_channels.py /api/social-channels (قنوات النشر: فيسبوك/إنستغرام/تيليجرام)
│   ├── jobs/                  نقاط دخول الطرفية: publish_due وposts (النشر التلقائي)
│   │   └── webhook.py         /webhook (واتساب)
│   ├── channels/            التحدث مع المصانع عبر قنوات المحادثة
│   │   ├── base.py            العقد: IncomingEvent و ChannelClient
│   │   ├── factory_flow.py    سير المحادثة المشترك بين كل القنوات
│   │   ├── texts.py           نصوص الردود العربية
│   │   └── whatsapp/          adapter.py (حمولة ميتا ← حدث موحّد) و client.py (إرسال/تنزيل)
│   └── services/            منطق العمل (لا يعرف HTTP ولا أي قناة)
│       ├── errors.py          NotFound / Duplicate / Conflict / InvalidInput
│       ├── products.py        CRUD المنتجات، parse_caption، upsert، import_csv
│       ├── factories.py       المصانع وجهات الاتصال، find_by_contact
│       ├── social_channels.py قنوات النشر ومعرّفاتها الأصلية (CRUD، list_active)
│       ├── product_intake.py  استقبال منتج من مصنع (تحقق، سؤال التحديث، حفظ، مزامنة)
│       ├── import_job.py      الاستيراد التلقائي كمهمة إدارية (مع قفل)
│       ├── media.py           حفظ الصور على القرص وبناء الروابط العامة
│       ├── media_scan.py / media_import.py / import_report.py   فحص المجلد + CSV واستيراده
│       ├── meta.py            مزامنة كتالوج ميتا (Graph API)
│       ├── admin_command.py   التعرف على أمر المدير النصي
│       └── dedup.py           منع معالجة الرسائل المكررة
├── alembic/                 ترحيلات المخطط (versions/)
├── scripts/import_from_media.py   استيراد المنتجات من مجلد الصور وCSV (طرفية)
├── scripts/make_videos.py         بناء ريلز المنتجات من صورها بـ ffmpeg (طرفية)
├── tests/                   اختبارات الوحدة (unittest)
├── test_api.py              فاحص مسارات الـ API على خادم يعمل (ليس اختبار وحدة)
├── requirements.txt
└── .env.example
```

الاتجاه المسموح للاستيراد: `routers, channels → services → models/core`. يحرسه `tests/test_architecture.py`.

---

## 2. التجهيز لأول مرة

```bash
# 1) بيئة افتراضية وتثبيت الاعتماديات
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2) ملف الإعدادات
cp .env.example .env
nano .env        # املأ القيم (انظر الجدول أدناه)

# 3) إنشاء قاعدة البيانات / ترحيلها إلى آخر نسخة
alembic upgrade head
```

> **مهم:** `alembic` يقرأ رابط القاعدة من `alembic.ini` (`sqlalchemy.url = sqlite:///./hojrat_bladi.db`) وليس من `.env`. شغّله من جذر المشروع، وإن غيّرت `DATABASE_URL` في `.env` فغيّرها في `alembic.ini` أيضاً.
> قبل ترحيل على قاعدة فيها بيانات حقيقية: `cp hojrat_bladi.db hojrat_bladi.db.bak-$(date +%Y%m%d-%H%M%S)`.

### المتغيرات الإلزامية (يتوقف التشغيل إن نقص أحدها)

`VERIFY_TOKEN`, `META_APP_SECRET`, `META_ACCESS_TOKEN`, `META_CATALOG_ID`, `WHATSAPP_PHONE_NUMBER_ID`, `ADMIN_API_KEY`, `BASE_URL`, `IMAGE_BASE_URL`, `MEDIA_ROOT`.
`MEDIA_ROOT` يجب أن يكون مجلداً موجوداً وقابلاً للكتابة (لا يُنشأ تلقائياً). أمثلة وقيم افتراضية لباقي المتغيرات في `.env.example`.

للتجربة المحلية بلا حسابات ميتا يكفي وضع قيم وهمية (غير فارغة) للمتغيرات أعلاه، مع مجلد حقيقي لـ `MEDIA_ROOT`:

```bash
mkdir -p ~/media/products
```

---

## 3. تشغيل المشروع

```bash
source venv/bin/activate

# تطوير (إعادة تحميل تلقائية)
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

# إنتاج (خلف Nginx، عبر systemd)
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

التحقق:

```bash
curl http://127.0.0.1:8000/health          # {"status":"ok"}
# التوثيق التفاعلي: http://127.0.0.1:8000/docs
```

### الاستيراد من مجلد الصور وCSV

```bash
python -m scripts.import_from_media                  # تجربة فقط، لا يكتب شيئاً
python -m scripts.import_from_media --apply          # كتابة فعلية (مع نسخة احتياطية للقاعدة)
python -m scripts.import_from_media --apply --sync   # ثم مزامنة ميتا
python -m scripts.import_from_media --help           # --factory-id --csv --verbose
```

يحتاج `PRODUCTS_CSV_PATH` و`DEFAULT_FACTORY_ID` في `.env` (أو الخيارين `--csv` و`--factory-id`)، ومصنعاً موجوداً في القاعدة.

### تسجيل مصنع وربط قنواته (مثال)

```bash
KEY=<ADMIN_API_KEY>; API=http://127.0.0.1:8000

# إنشاء مصنع (رقمه يصبح جهة واتساب تلقائياً)
curl -X POST $API/api/factories/ -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"مصنع الاختبار","activity":"صناعة الحجر الطبيعي","phone_number":"+213550000000"}'

# ربط تيليجرام (معرّف المستخدم الرقمي)
curl -X POST $API/api/factories/1/contacts -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"channel":"telegram","identifier":"123456789"}'

# محادثة Ollama: يرسل تعليمات المشروع ونشاط المصنع وآخر خمسة تبادلات.
curl -X POST $API/api/chat/ -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"factory_id":1,"prompt":"اكتب وصفاً قصيراً لهذا المنتج"}'
```

يستخدم المسار `OLLAMA_BASE_URL` (افتراضياً `http://localhost:11434`) والنموذج `OLLAMA_MODEL` (افتراضياً `qwen2.5:3b`). يرسل تعليمات عامة ثابتة، واسم المصنع ونشاطه، وآخر خمسة أزواج سؤال/جواب فقط، بالإضافة إلى السؤال الجديد. أضف النشاط أو حدّثه عبر `POST/PATCH /api/factories/`. يتطلب تشغيل Ollama على نفس الجهاز/بيئة الشبكة التي يعمل فيها التطبيق وتحميل النموذج. إذا تعذر الاتصال أو لم يرد Ollama بنص، يرجع المسار `502`. طبّق `alembic upgrade head` لإضافة سجل المحادثات ونشاط المصنع إلى قاعدة البيانات.

### مكتبة وسائط المصنع

تُحفظ روابط الصور والفيديوهات في جدول `media` المرتبط بالمصنع. جميع المسارات التالية محمية بـ `X-API-Key`:

| المسار | الوظيفة |
| --- | --- |
| `POST /api/factories/{factory_id}/media` | إضافة وسيط بصيغة JSON: `media_type` (`image` أو `video`)، `url` (رابط HTTP/HTTPS)، و`description` اختياري |
| `POST /api/factories/{factory_id}/media/upload` | رفع ملف بصيغة `multipart/form-data` مع الحقل `file`، والوصف اختياري؛ يُحفظ تحت `FACTORY_MEDIA_ROOT/{factory_id}/library` (الافتراضي `/home/ammar/media/factory/{factory_id}/library`)، ويُقبل JPG/JPEG/PNG/WEBP/GIF/ICO/SVG أو MP4/MOV/WEBM حتى 100 ميغابايت |
| `GET /api/factories/{factory_id}/media` | عرض وسائط المصنع، مع `?media_type=image` أو `?media_type=video`، و`skip` و`limit` للتصفح |
| `DELETE /api/factories/{factory_id}/media/{media_id}` | حذف وسيط من مكتبة المصنع المحدد |

مكان الحفظ: مجلد مخصص `factory` بجانب `products` (الافتراضي شقيق `MEDIA_ROOT`، ويُغيَّر بـ `FACTORY_MEDIA_ROOT` في `.env`). الروابط العامة لا تتغير لأنها تعتمد رقم المصنع واسم الملف لا المسار على القرص.
**لنقل الملفات التي حُفظت سابقًا في `/home/ammar/media/{رقم}/`:**
```bash
sudo systemctl stop <خدمة-fastapi>                 # اختياري لكن أفضل
python -m scripts.move_factory_media               # عرض فقط
python -m scripts.move_factory_media --apply       # تنفيذ (لا يستبدل ملفاً موجوداً، ولا يلمس غير ملفات المكتبة)
sudo systemctl start <خدمة-fastapi>
```
المجلد لازم يكون قابلاً للكتابة بواسطة مستخدم تشغيل التطبيق. رابط الملف المرفوع يُبنى من `BASE_URL` ويُقدّم عبر `/api/media/factories/{factory_id}/{filename}`. تبقى إضافة رابط خارجي عبر مسار `POST` الأساسي مدعومة. طبّق `alembic upgrade head` لإنشاء الجدول.

لتسجيل روابط صور المنتجات الموجودة لمصنع رقم 4 في مكتبة الوسائط، شغّل من جذر المشروع:

```bash
python -m scripts.register_factory_product_media          # معاينة دون كتابة
python -m scripts.register_factory_product_media --apply  # التسجيل الفعلي
```

يفحص السكريبت الرابط الأساسي والروابط الإضافية، ويتخطى الروابط المسجلة مسبقاً والمكررة. تُسجّل الروابط كصور (`image`) مع رمز المنتج واسمه في الوصف.

---

## 4. تشغيل الاختبارات (وحدة)

لا تحتاج خادماً ولا شبكة ولا قاعدة بيانات حقيقية:

```bash
source venv/bin/activate

python -m unittest discover -s tests -v        # كل الاختبارات
python -m unittest tests.test_factory_flow -v  # ملف واحد
python -m unittest tests.test_architecture -v  # حراس فصل الطبقات
```

| الملف | يغطي |
|---|---|
| `test_architecture.py` | اتجاه الاستيراد بين الطبقات |
| `test_factory_flow.py` | سير محادثة المصنع بقناة وهمية (واتساب وتيليجرام) |
| `test_whatsapp_adapter.py` | تحويل رسائل واتساب إلى أحداث موحّدة |
| `test_admin_command.py` | أمر المدير وتقييد المرسلين |
| `test_config.py` | التحقق من الإعدادات، `ADMIN_CONTACTS`، رابط المنتج |
| `test_media.py`, `test_media_scan.py`, `test_import_report.py` | الوسائط والاستيراد والتقرير |

---

## 5. فحص الـ API (`test_api.py`)

سكربت يفحص مسارات خادم **يعمل فعلاً** (محلي أو الإنتاج). الوضع الافتراضي **قراءة فقط**: يقرأ OpenAPI ويفحص حماية مسارات الإدارة والمسارات العامة والتحقق من المدخلات وwebhook بتوقيع خاطئ. يكتب النتائج في `results.json` و`results.md`.

شغّل الخادم أولاً (الفقرة 3)، ثم في طرفية أخرى:

```bash
export API_KEY='<نفس قيمة ADMIN_API_KEY>'

# قراءة فقط (آمن)
python3 test_api.py http://127.0.0.1:8000
python3 test_api.py https://api.hadjretbladi.com

# خيارات
python3 test_api.py http://127.0.0.1:8000 --sku B001 --product-id 1   # منتج موجود للفحوص القرائية
python3 test_api.py http://127.0.0.1:8000 --key '<KEY>'               # بدل المتغير

# دورة CRUD كاملة على مصنع ومنتج ومنشور تجريبية ثم حذفها (على قاعدة تطوير فقط)
python3 test_api.py http://127.0.0.1:8000 --write --yes

# يضيف اختبار مزامنة منتج واحد (قد يتصل بميتا فعلاً)
python3 test_api.py http://127.0.0.1:8000 --write --yes --include-sync
```

لا ينفّذ أبداً: `DELETE /api/products/clear-all?confirm=true`، ولا `POST /api/products/sync-meta/all`، ولا رفع CSV صالح، ولا webhook بتوقيع صحيح.
`--write` بدون `--yes` يسألك للتأكيد (`y`) قبل الكتابة. لا تشغّل `--write` على قاعدة الإنتاج.

---

## النشر التلقائي (فيسبوك وإنستغرام)

سكريبت يعمل كل دقيقة وينشر في كل تشغيل حتى `PUBLISH_PER_RUN` (10) منشوراً جاهزاً حان موعده. السقف اليومي لكل قناة على حدة (`PUBLISH_DAILY_LIMIT_INSTAGRAM` و`PUBLISH_DAILY_LIMIT_FACEBOOK`) ويُحسب من النشر الفعلي في آخر 24 ساعة، والمنشور الذي بلغت قناته سقفها ينتظر دون تعطيل غيره. المصدر: جدول `posts`. الكود: `app/services/social/` (محوّل لكل منصة) و`app/services/post_publisher.py` (الحجز والحالات والتقارير) و`app/jobs/` (نقطتا الدخول).

```bash
# 1) نسخة احتياطية من القاعدة بختم زمني ثم الترحيل
cp hojrat_bladi.db "hojrat_bladi.db.$(date +%Y%m%d-%H%M%S).bak"
alembic upgrade head

# 2) سجّل قنوات النشر في /api/social-channels (انظر أدناه). إن كان META_PAGE_ID أو META_IG_USER_ID مضبوطاً
#    في .env فيسجّلهما الترحيل تلقائياً كقناتين. ثم أعد تشغيل FastAPI

# 3) دورة العمل
python -m app.jobs.posts list --status draft
python -m scripts.compose_post B001 --apply  # مسودة من منتج (SKU) لكل القنوات المفعّلة، أو --channel 1 --channel 3
python -m app.jobs.posts publish 12          # نشر فوري لمنشور بعينه (يتجاوز الموعد والموافقة)
python -m app.jobs.posts approve 12          # draft/scheduled/failed -> approved
python -m app.jobs.publish_due --dry-run     # يعرض ما سيُنشر ونتيجة التحقق المسبق، بلا حجز
python -m app.jobs.publish_due               # نشر فعلي
python -m app.jobs.posts show 12             # حالة كل منصة وآخر خطأ
```

**مسارات الـ API (كلها بـ `X-API-Key`):**

| المسار | الوظيفة |
| --- | --- |
| `GET /api/posts/` | كل المنشورات |
| `GET /api/posts/published` | المنشورة فقط (الأحدث نشرًا أولًا)، مع `limit` و`offset` |
| `GET /api/posts/unpublished` | غير المنشورة: draft وscheduled وapproved وpublishing وfailed (المؤرشفة مستثناة) |
| `POST /api/posts/` | إنشاء، ويقبل `channel_ids: [1, 3]` (معرّفات `social_channels.id`) لتحديد القنوات |
| `PATCH /api/posts/{id}` | تعديل، ومع `channel_ids` تُستبدل القنوات (الناجحة منها تبقى كسجل) |
| `GET /api/posts/{id}` | منشور واحد مع `deliveries` (قناة وحالة كل وجهة: `channel_id` و`platform` و`channel_name`) |
| `POST /api/posts/{id}/publish` | نشر يدوي فوري: يرجع 202 بحالة publishing والنشر في الخلفية؛ تابعه بـ GET السابق. يتجاوز الموعد والسقف اليومي، ويرفض المنشور أو قيد النشر بـ 409 |

**قنوات النشر (`/api/social-channels`، بـ `X-API-Key`):** صفحة فيسبوك أو حساب إنستغرام احترافي أو قناة تيليجرام تُسجَّل في الجدول `social_channels` بدل `META_PAGE_ID` و`META_IG_USER_ID` في `.env`، فرمز ميتا نفسه يخدم أي صفحة تُضاف إلى الحافظة.

| المسار | الوظيفة |
| --- | --- |
| `POST /api/social-channels/` | إنشاء: `platform` (`facebook` / `instagram` / `telegram`) و`name` و`remote_id` (المعرّف الأصلي عند المنصة) و`is_active` (الافتراضي true) |
| `GET /api/social-channels/` | القائمة، مع تصفية اختيارية `?platform=` و`?is_active=` |
| `GET /api/social-channels/{id}` | قناة واحدة |
| `PATCH /api/social-channels/{id}` | تعديل `name` أو `remote_id` أو `is_active` (التفعيل/التعطيل). المنصة لا تتغير |
| `DELETE /api/social-channels/{id}` | حذف. يُرفض بـ 409 إن كانت للقناة منشورات، فعطّلها بدل حذفها |

صيغة `remote_id`: فيسبوك وإنستغرام رقم فقط؛ تيليجرام رقم مثل `-1001234567890` أو `@اسم_القناة`. التكرار (نفس المنصة والمعرّف) يرجع 400.

```bash
curl -X POST $API/api/social-channels/ -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"platform":"facebook","name":"صفحة حجرة بلادي","remote_id":"123456789012345"}'
curl -X PATCH $API/api/social-channels/1 -H "X-API-Key: $KEY" -H "Content-Type: application/json" -d '{"is_active":false}'
```

**قنوات المنشور (وجهات النشر):** المنشور الواحد يستهدف أي عدد من القنوات، وتُخزَّن وجهاته في `post_deliveries` بمعرّف القناة الداخلي `channel_id` (FK إلى `social_channels.id`) فقط. المنصة والمعرّف الأصلي واسم القناة وحالة التفعيل تُقرأ من جدول القنوات ولا تتكرر في المنشور.

- تُحدَّد القنوات عند الإنشاء بـ `channel_ids` (أو `scripts.compose_post --channel`) وتُحفظ صراحةً، فلا تتغير وجهة منشور بتفعيل قناة جديدة بعده.
- الوجهة تبدأ `pending` ثم `published` أو `failed`، وإعادة المحاولة تنشر القنوات غير الناجحة وحدها.
- قناة عُطّلت بعد تحديدها يفشل تسليمها بـ `channel_inactive:<منصة>:<معرّف>` ولا يتوقف باقي القنوات.
- لكل قناة نصها الخاص في `post_deliveries.caption` (قالب منصتها)، وبدونه يُنشر `posts.description`. القوالب في `app/services/post_templates.py` (فيسبوك بروابط، إنستغرام بلا روابط)، ورقم واتساب من `POST_CONTACT_PHONE`.
- لا رجوع إلى `META_PAGE_ID` / `META_IG_USER_ID` وقت النشر: منشور بلا قنوات يفشل بـ `no_channels`.

**منشورات تجريبية:**

```bash
python -m scripts.seed_posts                                  # عرض فقط
python -m scripts.seed_posts --apply --video-url <MP4_URL>    # مسودات (الفيديو اختياري)
python -m scripts.seed_posts --apply --approve --with-failures
python -m scripts.seed_posts --clear --apply                  # حذف منشورات [TEST-n] من القاعدة
```
تنبيه: `--approve` ثم تشغيل الناشر ينشر فعلًا على الصفحة الحقيقية.


- **الحالات:** `draft` ← `approved` ← `publishing` ← `published` أو `failed`. النص هو `posts.description`، وقنواته في `post_deliveries`، وأنواع الوسائط `text|image|reel` (`gallery` غير مدعوم في هذه المرحلة).
- **إعادة محاولة الفاشل:** `python -m app.jobs.posts approve <id>` فتُنشر القناة الفاشلة وحدها (الناجحة محفوظة في `post_deliveries`).
- **العالقون:** منشور بقي `publishing` أكثر من `PUBLISH_STALE_MINUTES` لا يُعاد نشره تلقائياً (ربما نُشر)؛ يُكتب له تقرير في `publish_reports` ويظهر في مخرجات التشغيل.
- **التقارير:** `publish_reports.kind` = `token` (كود 190/463/467) أو `media` أو `limit` أو `network` أو `unknown`، و`sent_at` فارغ حتى تُبنى قناة الإرسال.
- **الجدولة:** ملفات systemd في `deploy/systemd/` (المؤقت يعمل كل دقيقة)؛ انظر التفعيل في أعلى كل ملف ووثيقة المرحلة.

---

## توليد فيديو المنتج (ريلز) بـ ffmpeg

يبني ريلز **متوافقًا مع إنستغرام: MP4 (H.264 High, yuv420p)، 9:16 بدقة 1080×1920، 30 إطارًا/ث، حوالي 7 ثوانٍ، +faststart** من صور المنتج
(حتى 6 صور بترتيب أرقامها): تكبير/تصغير تدريجي لكل صورة + انتقال متداخل بينها. بلا صوت، أو بصوت AAC ستيريو 48kHz عند دمج ملف صوتي.
**يُحفظ دائمًا في آخر تسلسل الفيديوهات ولا يستبدل أي فيديو:** `{MEDIA_ROOT}/{الفئة}/{SKU}/{n}.mp4` حيث n = آخر رقم فيديو موجود + 1
(1.mp4 ثم 2.mp4 ...، وتُحتسب الفيديوهات المرفوعة يدويًا أيضًا). يُخدم بالرابط `{IMAGE_BASE_URL}/{SKU}/{n}.mp4`
(قاعدة Nginx الحالية تسمح بـ `.mp4`)، ويصبح الأحدث `products.video_url`. الغلاف (poster) هو الصورة الرئيسية `1.jpg` نفسها.
الحجم الأقصى 10MB (يعيد الضغط تلقائيًا مرة بجودة أقل إن زاد؛ الحالة الواقعية 1 إلى 3MB). بناء الفيديو يستغرق ~10 إلى 20 ثانية.

**المتطلب:** `ffmpeg` و`ffprobe` على الخادم (`sudo apt install ffmpeg`).

**الترحيل (مرة واحدة، بعد نسخ القاعدة احتياطياً):**
```bash
cp hojrat_bladi.db hojrat_bladi.db.bak-$(date +%Y%m%d-%H%M%S)
alembic upgrade head        # يضيف العمود video_url (قابل للفراغ، لا يلمس البيانات)
```

**من الطرفية:**
```bash
python -m scripts.make_videos B001                      # منتج واحد (كل تشغيل يضيف فيديو جديدًا برقم تالٍ)
python -m scripts.make_videos --all --dry-run           # ماذا سيُبنى (المنتجات التي لا فيديو لها)
python -m scripts.make_videos --all                     # كل منتج لا فيديو له (من له فيديو يُتجاوز)
python -m scripts.make_videos B001 --fit crop --duration 8
```

**من الـ API** (مفتاح `X-API-Key`):
```bash
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" \
  "https://api.hadjretbladi.com/api/products/by-sku/B001/video?duration=7&fit=blur"
```
`fit=blur` (الافتراضي): الصورة كاملة فوق خلفية مموّهة منها (مناسب لصور المنتجات الأفقية)، و`fit=crop`: ملء الإطار بالقص.
الاستجابة تتضمن `index` (رقم الفيديو الجديد) و`video_url`. الفيديو لا يدخل كتالوج ميتا ولا يغيّر `sync_status`.
`video_url` يظهر في `GET /api/products` و`/api/products/{sku}` ليستعمله فيد Next.js (`autoplay muted loop playsinline` و`poster` = `primary_media_url`).

**دمج صوت:** ضع ملفات الصوت (mp3/m4a/aac/wav/ogg) في مجلد **خارج `MEDIA_ROOT`** (مثل `/home/ammar/media/audio`) واضبطه في `.env`:
`AUDIO_ROOT=/home/ammar/media/audio`. لا يحتاج Nginx ولا Syncthing أن «يلتقطه»: ffmpeg يقرؤه من القرص ولا يُخدم إلا الـ MP4 الناتج.
```bash
python -m scripts.make_videos --list-audio
python -m scripts.make_videos B001 --audio music1.mp3
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" "https://api.hadjretbladi.com/api/products/by-sku/B001/video?audio=music1.mp3"
```
الصوت يتكرر إن كان أقصر من الفيديو ويُقص عند نهايته مع ظهور/اختفاء تدريجي (AAC 128kbps، 48kHz). `GET /api/products/audio-files` يعرض المتاح.
تنبيه: الفيد على الموقع يشغّل الفيديو صامتًا (`muted`)، فالصوت مفيد أساسًا عند نشر الريلز على إنستغرام وفيسبوك.

الكود: `app/services/video_maker.py` (ffmpeg فقط، لا قاعدة بيانات) و`products.generate_product_video` (يحفظ الرابط).

---

## 6. ملاحظات

- مسارات القراءة العامة: `GET /api/products/` و`GET /api/products/{sku}`. مسارات الإدارة تتطلب ترويسة `X-API-Key`.
- الصور تخدمها Nginx مباشرة من `MEDIA_ROOT` (لا FastAPI)، والرابط العام `{IMAGE_BASE_URL}/{SKU}/{n}.{ext}`.
- عند النشر على إنستغرام، يجلب التطبيق الصورة ويحوّلها إلى JPEG ضمن مجال الأبعاد المدعوم (نسبة 4:5 إلى 1.91:1، وعرض 320–1440 بكسل). تُقص الصورة من الوسط عند الحاجة، وتُحفظ نسخة مشتقة مستقلة عن الأصل وتُخدم من `/api/media/instagram/{sha256}.jpg` عبر واجهة API.
- عند نشر Reel على إنستغرام، يُفحص الفيديو ثم يُحوّل إلى إطار عمودي 1080×1920 بقصّ مركزي وترميز H.264/AAC، وتُخدم النسخة المشتقة من `/api/media/instagram/{sha256}.mp4`. يتطلب ذلك تثبيت `ffmpeg` و`ffprobe` وإتاحتهما في `PATH` على خادم التطبيق (على Debian/Ubuntu: `sudo apt-get install ffmpeg`).
- لملء `category_code` من الحروف الأولى في SKU (مثل `B001` ← `B` و`PTK012` ← `PTK`)، شغّل `python -m scripts.update_category_codes` لمعاينة التغييرات، ثم `python -m scripts.update_category_codes --apply` لحفظها. تُتخطى رموز SKU التي لا تطابق صيغة حروف كبيرة متبوعة بأرقام.
- نوقش تبديل الاسمين الفيزيائيين لعمودي قاعدة البيانات `title` و`category_code`، لكن لا يوجد ترحيل Alembic لهذا التبديل في الحالة الحالية للمستودع؛ لا تشغّل `alembic upgrade head` لهذا الغرض.
- إضافة قناة جديدة أو ميزة جديدة: انظر `ARCHITECTURE.md`.


---

## بيانات الاسم التجاري والمقاسات والوسوم من XLSX

يضيف الترحيل `c6f2a1d9e843` الحقلين الاختياريين `commercial_name` و`sizes` إلى المنتجات. يقرأ الـSeed أيضاً `description` والوسوم من ملف Excel؛ ولا يغيّر `category_code`.

بعد أخذ نسخة احتياطية من قاعدة البيانات، شغّل الترحيل ثم نفّذ Seed من جذر المشروع:

```bash
alembic upgrade head
python -m scripts.seed_product_catalog --dry-run
python -m scripts.seed_product_catalog
# لإعادة حفظ الوسوم القديمة بصيغة JSON تظهر حروفها العربية مباشرة (مرة واحدة عند الحاجة)
python -m scripts.seed_product_catalog --rewrite-tag-encoding
```

ضع `product_catalog_seed.xlsx` في جذر المشروع؛ هذا هو مصدر البيانات الافتراضي. ويمكن تمرير ملف آخر عبر `--xlsx /path/to/file.xlsx`. يطابق السكربت الصفوف حسب SKU ويحدّث المنتجات الموجودة فقط، ولا ينشئ منتجات جديدة. يستورد الاسم التجاري والمقاس والوصف والوسوم؛ والحقول الفارغة في Excel لا تمسح القيم الموجودة في قاعدة البيانات. الوسوم تُقرأ من عمود «الوسوم»، أو تُشتق من أعمدة الصنف والنوع واللون والملمس والمقاس والاستخدام والسطح عند غيابه. لا يتغيّر التصنيف بالكود. كُوّن محرك قاعدة البيانات أيضاً ليكتب المحارف العربية داخل قيم JSON بصيغتها المقروءة؛ خيار `--rewrite-tag-encoding` يعيد حفظ الوسوم الموجودة دون تغيير قيمها، كي ينعكس ذلك على التخزين القديم.

عند المزامنة مع Meta يبقى `title` هو اسم المنتج (`name`)، ويُرسل `sizes` إلى `size` وأول خمسة عناصر من `tags` إلى `custom_label_0` حتى `custom_label_4`. لا يُرسل `category_code` إلى Meta.

## ملخص تنفيذ جلسة 21 أكتوبر 2026

### تجهيز صور وفيديوهات Instagram

- قبل نشر صورة، ينزّلها التطبيق من رابطها، يقرأ اتجاه EXIF، يقصّ من الوسط إذا كانت نسبة الأبعاد خارج المجال `4:5` إلى `1.91:1`، ويضبط العرض إلى `320–1440` بكسل. الناتج JPEG حتى لو كانت الصورة الأصلية PNG أو WebP. لا تتغير الصورة الأصلية.
- قبل نشر Reel، يفحص التطبيق مسار الفيديو بواسطة `ffprobe`. إذا لم تكن أبعاده `1080×1920`، ينشئ نسخة MP4 عمودية بهذه الأبعاد بقص مركزي وترميز H.264/AAC. إذا كانت الأبعاد مطابقة، يستخدم رابط المصدر كما هو.
- تحفظ النسخ المعالجة في `MEDIA_ROOT/.instagram` باسم مبني على SHA-256، وتُقدّم عبر `GET /api/media/instagram/{sha256}.jpg` أو `GET /api/media/instagram/{sha256}.mp4`. يجب أن يكون `BASE_URL` عامًا ويمكن لميتا الوصول إليه، وأن يمرّر الخادم الوكيل مسارات `/api/` إلى التطبيق.
- يتطلب تحويل الفيديو وجود `ffmpeg` و`ffprobe` في `PATH` على خادم التطبيق. في Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install ffmpeg
ffmpeg -version
ffprobe -version
```

في بيئة التطوير لهذه الجلسة لم تتوفر صلاحية تثبيت FFmpeg؛ لذلك اختُبرت عملية التحويل بمضاعفات اختبارية، ولم يُجر اختبار ترميز فعلي لفيديو.

لتثبيت اعتماد معالجة الصور في بيئة المشروع:

```bash
python -m pip install -r requirements.txt
```

### إنشاء منشورات المنتجات الجاهزة للمؤقت

المسارات الإدارية تتطلب ترويسة `X-API-Key`. تُحفظ المنشورات المنشأة بحالة موافَق عليها، ويضيف لكل قناة تسليمًا بحالة انتظار، ليتمكن المؤقت من التقاطها دون موافقة يدوية:

```bash
# عدة منتجات حسب SKU وعدة قنوات محلية (times اختياري هنا: بدونه يُنشر فوراً)
curl -X POST "$BASE_URL/api/posts/prepare/by-sku" \
  -H "X-API-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"factory_id":1,"channel_ids":[1,2],"skus":["B001","PTK012"]}'

# جميع منتجات المصنع، موزَّعة على ساعات يومية (8 و14 و18 بتوقيت الجزائر)
curl -X POST "$BASE_URL/api/posts/prepare/all" \
  -H "X-API-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"factory_id":1,"channel_ids":[1,2],"times":[8,14,18]}'

# المنتجات التابعة إلى عدة تصنيفات (times إلزامي أيضاً)
curl -X POST "$BASE_URL/api/posts/prepare/by-category" \
  -H "X-API-Key: $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"factory_id":1,"channel_ids":[1,2],"category_codes":["B","PTK"],"times":[8,14,18],"start_date":"2026-10-25"}'
```

**الجدولة (`times`):** ساعات النشر اليومية بتوقيت `PUBLISH_TIMEZONE` (الجزائر افتراضياً، UTC+1)، وتقبل `[8,14,18]` أو `"8,14,18"` أو `["08:30"]`. تُوزَّع المنشورات عليها بالترتيب يوماً بعد يوم (6 منشورات مع 3 أوقات = يومان)، وتُحفظ في `scheduled_at` بتوقيت UTC. `start_date` اختياري (الافتراضي اليوم، وما فات من مواعيده يُتخطى). المواعيد المحجوزة لمنشورات غير منشورة لنفس المصنع تُتخطى، فالدفعة الجديدة تلحق بآخر الطابور. `times` إلزامي في `prepare/all` و`prepare/by-category` كي لا تُنشر الدفعة كلها فور إنشائها؛ وفي `prepare/by-sku` اختياري (بدونه يُنشر في أقرب دقيقة).

`channel_ids` هي معرّفات الصفحات/القنوات المحلية في جدول `social_channels`، وليست معرّفات الصفحات الأصلية لدى المنصة. ترجع الاستجابة عدد المنشورات ومعرّفاتها. مسار الفئة يختار المنتجات المسجلة فعلًا برمز الفئة؛ شغّل سكريبت التصنيف أدناه أولًا إذا كانت قيم `category_code` لم تُملأ.

### ملء تصنيف المنتجات من SKU

يستخرج السكريبت الحروف الكبيرة المتتابعة في بداية SKU المطابق لصيغة حروف كبيرة ثم أرقام: `B001` ← `B` و`PTK012` ← `PTK`. يتخطى SKU غير المطابق، ويعرض المعاينة دون حفظ افتراضيًا:

```bash
python -m scripts.update_category_codes
python -m scripts.update_category_codes --apply
```

نفّذ المعاينة أولًا وراجع القيم قبل إضافة `--apply`.

### مزامنة بيانات المنتج مع Meta

- يبقى `title` هو قيمة حقل Meta `name`؛ لا يستبدله `commercial_name`.
- يرسل `sizes` إلى `size` عند وجود قيمة.
- يرسل أول خمسة وسوم نصية غير فارغة إلى `custom_label_0` حتى `custom_label_4`.
- لا يرسل `category_code` إلى Meta.

### فحوصات التطوير التي نُفّذت

```bash
python -m unittest tests.test_social_adapters -q
python -m unittest tests.test_meta -q
python -m unittest tests.test_update_category_codes -q
python -m unittest tests.test_product_column_name_swap_migration tests.test_update_category_codes tests.test_meta -v
python -m unittest discover -s tests -q
git diff --check
```

اجتازت مجموعة الاختبارات الكاملة `187` اختبارًا في آخر تشغيل. يتضمن ذلك اختبارات مسارات المنشورات، إعداد صور Instagram، تدفق الفيديو وإتاحة النسخ المشتقة، حمولة Meta، وسكريبت تصنيف المنتجات.

### ملاحظة حول أسماء أعمدة قاعدة البيانات

نوقش تبديل الاسمين الفيزيائيين للعمودين `title` و`category_code` مع الإبقاء على البيانات. لا يوجد في الحالة الحالية للمستودع ترحيل Alembic يبدّل اسمي هذين العمودين؛ النموذج الحالي ما زال يستخدم أسماء الأعمدة المعتادة. لا تنفّذ ترحيلًا لهذا الغرض قبل إضافته ومراجعته.

### اضفنا ميزة حذف جميع المنشورات 

# 1) معاينة فقط (يعرض العدد دون حذف)	
python -m app.jobs.delete_posts

# 2) حذف جميع المنشورات	
python -m app.jobs.delete_posts --yes

# اختياري: حذف حالة معيّنة فقط (draft/failed/published/…)	
python -m app.jobs.delete_posts --status failed --yes
