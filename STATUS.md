# حالة المشروع — الخلفية (FastAPI)

الواجهة المقابلة: `../ikhlasdz` (Next.js)، ولها `STATUS.md` خاص بها.

## الميزة الحالية
- تقسيم أذونات Facebook Login حسب الخدمة، بدءاً بخدمة «توليد الفيديو والتعليقات»:
  اختيار صفحة واحدة فقط، وطلب أربعة أذونات لا غير
  (`pages_show_list`، `pages_manage_posts`، `pages_read_engagement`، `pages_manage_engagement`)،
  ثم التحويل إلى صفحة الخدمة.
- فرع العمل: `feat/facebook-service-permissions` (متفرع من `main` في المشروعين).

## الخلفية (FastAPI)
- [x] Schema مكتوب (`schemas.FacebookServiceStatus` وما معه، ونموذج `ServiceConnection`)
- [x] Endpoint جاهز (`/auth/facebook/login?service=`، `/auth/facebook/services/{service}` و`/select` و`/selection`)
- [x] الاختبار مرّ ✓ (`venv/bin/python -m pytest -q` ← 381 ناجحة، منها `tests/test_facebook_services.py`)

## الواجهة (Next.js)
- [x] Types معرّفة (`features/facebook-connect/service.ts`)
- [x] API call جاهز
- [x] المكوّن/الصفحة مكتملة (اختيار الصفحة في `/connect/done/`، وصفحة الخدمة `/services/facebook-reels/dashboard/`)
- [x] الاختبار البصري مرّ ✓ (محلياً مع فيسبوك مزيَّف؛ لم يُختبر مع فيسبوك الحقيقي)

## الميزات المنجزة
مدموجة في `main` (PR #1 من `security/private-docs-and-headers`، الدمج `4b42d51`) ومنشورة على السيرفر:
- حجب `/docs` و`/redoc` و`/openapi.json` خلف Basic Auth (كلمة السر = `ADMIN_API_KEY`).
- ترويسات الأمان على كل الردود (HSTS، nosniff، X-Frame-Options، Referrer-Policy).
- إزالة `pandas` غير المستعملة من `requirements.txt` (لا تتوافق نسختها مع Python 3.14).
- توحيد مدى الألوان في الريلز المولَّدة (إصلاح `yuvj420p` مع ffmpeg 7 فما فوق).

الاختبارات: `venv/bin/python -m pytest -q` ← 362 ناجحة (قبل بدء الميزة الحالية).

## الخطوة التالية
- الميزة الحالية غير محفوظة في git بعد (على الفرع `feat/facebook-service-permissions`)، وغير منشورة.
- عند النشر: `venv/bin/alembic upgrade head` (جدول `service_connections`، المراجعة `a4c7e1d9b3f5`) ثم إعادة تشغيل الخدمة.
- اختبار التدفق مع فيسبوك الحقيقي بعد النشر (نافذة الموافقة يجب أن تعرض الأذونات الأربعة فقط).
- بوت التعليقات على صفحات الزبائن يحتاج قراراً: استقبال التعليقات عبر webhook يتطلب `pages_manage_metadata`
  (ليس من الأذونات الأربعة)، والرد الخاص يتطلب `pages_messaging`. البديل بالأذونات الأربعة: جلب التعليقات
  دورياً (`pages_read_engagement`) والرد العلني (`pages_manage_engagement`). لذلك لم يعد الدخول يشترك في webhook.
- توليد الفيديو للزبون من صفحة الخدمة: لا endpoint له بعد (الموجود خاص بالمصانع).
- إعادة الربط بصفحة أخرى تنقل الخدمة إليها وتُبقي الصفحة القديمة في «الصفحات المربوطة» حتى يفكّها الزبون.
- الواجهة أعادت تنظيم مسار الوصول إلى الخدمة (2026-11-11) دون أي تغيير هنا: تعتمد على
  `GET /auth/facebook/services/{service}` (401 للزائر، و`page` و`candidates` للمسجّل).
- مؤجَّل (اقتراح المستخدم، التفاصيل في `../ikhlasdz/STATUS.md`): حماية الواجهة بـ `proxy.ts` تتطلب ضبط كوكي
  الجلسة على `.ikhlasdz.com` (اليوم بلا `domain` في `app/routers/auth.py` فيخص `api.ikhlasdz.com` وحده)،
  مع معالجة الجلسات القائمة عند النشر. ولا يوجد مفهوم اشتراك (مدفوع أو بتفعيل الإدارة): الوصول للّوحة بربط صفحة فقط.
- خادم فيسبوك المقلَّد للاختبار المحلي ما زال في مجلد مؤقت خارج المشروع؛ يُنقل إلى هنا عند كتابة اختبار Playwright.
- على السيرفر: `server_tokens off;` في nginx لإخفاء رقم الإصدار (لم يُؤكَّد تنفيذه).
- لم يُفحص: مسار إعادة ترميز الفيديو في `app/services/instagram_media.py` مع ffmpeg الحديث.

## ملاحظات البيئة المحلية
- `venv` مبنية على Python 3.14. التشغيل: `venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`.
- `.env` المحلي يسمح بـ `http://localhost:3000` في `ALLOWED_ORIGINS` للعمل مع الواجهة المحلية.
- تسجيل الدخول بفيسبوك لا يكتمل محلياً: عنوان العودة يشير إلى الإنتاج، و`META_APP_ID` غير مضبوط في `.env` المحلي.
- قاعدة البيانات المحلية طُبّقت عليها المراجعة `a4c7e1d9b3f5`. alembic يقرأ العنوان من `alembic.ini` لا من `DATABASE_URL`.

## ملفات محمية
- `.env` و`.env.*`
- `alembic/versions/`
- `deploy/systemd/`
