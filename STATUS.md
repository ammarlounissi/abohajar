# حالة المشروع — الخلفية (FastAPI)

الواجهة المقابلة: `../ikhlasdz` (Next.js)، ولها `STATUS.md` خاص بها.

## الميزة الحالية
- لا توجد ميزة قيد العمل. بانتظار تحديد الميزة التالية.

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
على الفرع `security/private-docs-and-headers` (مرفوع، غير مدموج في `main`، غير منشور على السيرفر):
- حجب `/docs` و`/redoc` و`/openapi.json` خلف Basic Auth (كلمة السر = `ADMIN_API_KEY`).
- ترويسات الأمان على كل الردود (HSTS، nosniff، X-Frame-Options، Referrer-Policy).
- إزالة `pandas` غير المستعملة من `requirements.txt` (لا تتوافق نسختها مع Python 3.14).
- توحيد مدى الألوان في الريلز المولَّدة (إصلاح `yuvj420p` مع ffmpeg 7 فما فوق).

الاختبارات: `venv/bin/python -m pytest -q` ← 362 ناجحة.

## الخطوة التالية
- دمج الفرع في `main` ثم إعادة تشغيل الخدمة على السيرفر.
- على السيرفر: `server_tokens off;` في nginx لإخفاء رقم الإصدار.
- لم يُفحص: مسار إعادة ترميز الفيديو في `app/services/instagram_media.py` مع ffmpeg الحديث.

## ملاحظات البيئة المحلية
- `venv` مبنية على Python 3.14. التشغيل: `venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`.
- `.env` المحلي يسمح بـ `http://localhost:3000` في `ALLOWED_ORIGINS` للعمل مع الواجهة المحلية.
- تسجيل الدخول بفيسبوك لا يكتمل محلياً: عنوان العودة يشير إلى الإنتاج.

## ملفات محمية
- `.env` و`.env.*`
- `alembic/versions/`
- `deploy/systemd/`
