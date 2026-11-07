# التغييرات (انسخ الملفات فوق المشروعين بنفس المسارات)

## abohajar (الباك إند)
- جدول `users`: دخول بالبريد وكلمة السر (scrypt) أو بفيسبوك — models.py، services/users.py، ترحيل 9a1b2c3d4e5f
- الجلسة: كوكي HttpOnly موقّع — services/sessions.py، routers/auth.py (/auth/register, /login, /logout, /me)، CORS مع credentials في main.py، SESSION_SECRET في config وملف .env.example
- ملكية الصفحات: `social_channels.user_id` (ترحيل 9b2c3d4e5f60)، و/auth/facebook/status يعرض صفحات المستخدم فقط
- فك الارتباط: DELETE /auth/facebook/pages/{page_id} (يلغي subscribed_apps ويمحو التوكن)
- حماية state بكوكي hb_oauth، وطلبا deauthorize/delete يزيلان هوية فيسبوك من المستخدم
- اختبارات: test_users.py، test_auth_session.py، وتعديل test_facebook_callback_redirect.py

## store.hadjretbladi (الواجهة)
- app/connect/connect-client.jsx: نموذج دخول/تسجيل + زر فيسبوك + جدول صفحاتي + زر «فك الارتباط»
- app/connect/done/done-client.jsx: رسالتا خطأ جديدتان

## بعد النسخ
1. `alembic upgrade head`
2. `pytest tests/test_users.py tests/test_auth_session.py tests/test_facebook_callback_redirect.py`
3. أضف SESSION_SECRET إلى .env (اختياري، وإلا يُشتق من META_APP_SECRET)
4. أضف صلاحية email في تطبيق ميتا
