# حالة المشروع — الخلفية (FastAPI)

الواجهة المقابلة: `../ikhlasdz` (Next.js)، ولها `STATUS.md` خاص بها.

## الميزة الحالية
- لا توجد ميزة قيد العمل. العمل يعود إلى `main` في المشروعين (محدَّث ومطابق لـ `origin/main` في 2026-11-11).
- المرشَّحان التاليان (انظر «الخطوة التالية»): توليد الفيديو للزبون، ثم بوت التعليقات بعد حسم طريقته.

## الخلفية (FastAPI)
- [ ] Schema مكتوب
- [ ] Endpoint جاهز
- [ ] الاختبار مرّ ✓

## الواجهة (Next.js)
- [ ] Types معرّفة
- [ ] API call جاهز
- [ ] المكوّن/الصفحة مكتملة
- [ ] الاختبار البصري مرّ ✓

## الميزات المنجزة
كلها مدموجة في `main` ومنشورة على السيرفر.

**Facebook Login حسب الخدمة** (PR #2 من `feat/facebook-service-permissions`، الدمج `2c667f5`، نُشر في 2026-11-11):
- `/auth/facebook/login?service=facebook-reels` يطلب أربعة أذونات فقط
  (`pages_show_list`، `pages_manage_posts`، `pages_read_engagement`، `pages_manage_engagement`).
  بدون `service`: دخول بالهوية فقط (`public_profile,email`) بلا صفحات.
- صفحة واحدة لكل مستخدم ولكل خدمة: جدول `service_connections` (المراجعة `a4c7e1d9b3f5`).
  صفحة واحدة ممنوحة تُربط مباشرة؛ عدة صفحات يختار المستخدم إحداها (مهلة 15 دقيقة)؛ رفض إذن أو لا صفحة = لا ربط.
- المسارات: `GET /auth/facebook/services/{service}`، `POST …/select`، `DELETE …/selection`.
  سجل الخدمات في `app/services/facebook_services.py` (خدمة جديدة = عنصر في `SERVICES`).
- الدخول لم يعد يشترك في webhook التعليقات (يتطلب `pages_manage_metadata`، خارج الأذونات الأربعة).
- خادم فيسبوك المقلَّد للتطوير صار داخل المشروع (`ca953d8`): `scripts/fake_facebook.py` وسيناريوه
  `scripts/fake_facebook_scenario.json`.
- الاختبارات: `venv/bin/python -m pytest -q` ← 381 ناجحة (منها `tests/test_facebook_services.py`).
- تحقق من الإنتاج (2026-11-11، بطلبات مباشرة): `GET /auth/facebook/services/facebook-reels` ← 401 للزائر،
  و`/auth/facebook/login?service=facebook-reels` يحوّل إلى فيسبوك بالأذونات الأربعة وحدها في `scope`.

**الأمان والريلز** (PR #1 من `security/private-docs-and-headers`، الدمج `4b42d51`):
- حجب `/docs` و`/redoc` و`/openapi.json` خلف Basic Auth (كلمة السر = `ADMIN_API_KEY`).
- ترويسات الأمان على كل الردود (HSTS، nosniff، X-Frame-Options، Referrer-Policy).
- إزالة `pandas` غير المستعملة من `requirements.txt` (لا تتوافق نسختها مع Python 3.14).
- توحيد مدى الألوان في الريلز المولَّدة (إصلاح `yuvj420p` مع ffmpeg 7 فما فوق).

## الخطوة التالية
- اختبار التدفق كاملاً مع فيسبوك الحقيقي على الإنتاج: نافذة الموافقة تعرض الأذونات الأربعة فقط، ثم ربط صفحة
  واحدة، ثم اختيار واحدة من عدة صفحات. لم يُجرَّب بعد إلا بفيسبوك مقلَّد محلياً. ربط الصفحة الفعلي هو أيضاً
  ما يؤكد أن `alembic upgrade head` طُبّق على السيرفر (جدول `service_connections`).
- توليد الفيديو للزبون من صفحة الخدمة: لا endpoint له بعد (الموجود خاص بالمصانع).
- بوت التعليقات على صفحات الزبائن يحتاج قراراً: استقبال التعليقات عبر webhook يتطلب `pages_manage_metadata`
  (ليس من الأذونات الأربعة)، والرد الخاص يتطلب `pages_messaging`. البديل بالأذونات الأربعة: جلب التعليقات
  دورياً (`pages_read_engagement`) والرد العلني (`pages_manage_engagement`).
- إعادة الربط بصفحة أخرى تنقل الخدمة إليها وتُبقي الصفحة القديمة في «الصفحات المربوطة» حتى يفكّها الزبون.
- مؤجَّل (اقتراح المستخدم، التفاصيل في `../ikhlasdz/STATUS.md`): حماية الواجهة بـ `proxy.ts` تتطلب ضبط كوكي
  الجلسة على `.ikhlasdz.com` (اليوم بلا `domain` في `app/routers/auth.py` فيخص `api.ikhlasdz.com` وحده)،
  مع معالجة الجلسات القائمة عند النشر. ولا يوجد مفهوم اشتراك (مدفوع أو بتفعيل الإدارة): الوصول للّوحة بربط صفحة فقط.
- على السيرفر: `server_tokens off;` في nginx لإخفاء رقم الإصدار (لم يُؤكَّد تنفيذه).
- لم يُفحص: مسار إعادة ترميز الفيديو في `app/services/instagram_media.py` مع ffmpeg الحديث.
- فروع محلية مدموجة في `main` ولم تُحذف بعد: `feat/facebook-service-permissions`، `security/private-docs-and-headers`، `cloude`.

## ملاحظات البيئة المحلية
- `venv` مبنية على Python 3.14. التشغيل: `venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`.
- `.env` المحلي يسمح بـ `http://localhost:3000` في `ALLOWED_ORIGINS` للعمل مع الواجهة المحلية.
- تسجيل الدخول بفيسبوك لا يكتمل محلياً: عنوان العودة يشير إلى الإنتاج، و`META_APP_ID` غير مضبوط في `.env` المحلي.
- لتجربة Facebook Login محلياً بفيسبوك مقلَّد: `venv/bin/python -m scripts.fake_facebook` (المنفذ 8000، قاعدة مستقلة
  `fake_facebook.db`)، أو الإعداد `api-fake-facebook` في `../.claude/launch.json`. لا يُشغَّل على السيرفر.
- قاعدة البيانات المحلية طُبّقت عليها المراجعة `a4c7e1d9b3f5`. alembic يقرأ العنوان من `alembic.ini` لا من `DATABASE_URL`.

## ملفات محمية
- `.env` و`.env.*`
- `alembic/versions/`
- `deploy/systemd/`
