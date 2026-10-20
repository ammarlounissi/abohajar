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
  -d '{"name":"مصنع الاختبار","phone_number":"+213550000000"}'

# ربط تيليجرام (معرّف المستخدم الرقمي)
curl -X POST $API/api/factories/1/contacts -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"channel":"telegram","identifier":"123456789"}'
```

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

سكريبت يومي ينشر منشوراً واحداً جاهزاً من جدول `posts`. الكود: `app/services/social/` (محوّل لكل منصة) و`app/services/post_publisher.py` (الحجز والحالات والتقارير) و`app/jobs/` (نقطتا الدخول).

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
- **الجدولة:** ملفات systemd في `deploy/systemd/` (07:00 UTC = 08:00 الجزائر)؛ انظر التفعيل في أعلى كل ملف ووثيقة المرحلة.

---

## 6. ملاحظات

- مسارات القراءة العامة: `GET /api/products/` و`GET /api/products/{sku}`. مسارات الإدارة تتطلب ترويسة `X-API-Key`.
- الصور تخدمها Nginx مباشرة من `MEDIA_ROOT` (لا FastAPI)، والرابط العام `{IMAGE_BASE_URL}/{SKU}/{n}.{ext}`.
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
