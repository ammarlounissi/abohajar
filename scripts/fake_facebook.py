"""خادم تطوير محلي: التطبيق الحقيقي مع فيسبوك مقلَّد، لتجربة Facebook Login دون تطبيق Meta.

    venv/bin/python -m scripts.fake_facebook            # http://127.0.0.1:8000
    venv/bin/python -m scripts.fake_facebook --port 8001

للتطوير المحلي فقط، ولا يُشغَّل على السيرفر: يستقبل على 127.0.0.1 وحده، ولا يتصل بفيسبوك إطلاقاً.

ما الذي يقلّده:
- نافذة موافقة فيسبوك: تُستبدل بتحويل مباشر إلى /auth/facebook/callback (كأن المستخدم وافق أو رفض).
- ردود Graph API (الهوية، الأذونات الممنوحة، الصفحات): تُقرأ من scripts/fake_facebook_scenario.json
  عند كل طلب، فتعديل الملف يكفي ولا حاجة لإعادة التشغيل.

ملف السيناريو:
    "granted": الأذونات التي «منحها» المستخدم (احذف واحداً لتجربة missing_permissions)
    "pages":   الصفحات التي «اختارها» (واحدة = ربط مباشر، عدة = صفحة الاختيار، لا شيء = no_pages)
    "deny":    true لتجربة رفض المستخدم للنافذة (access_denied)
    "publish_error": نص خطأ لتجربة فشل النشر، مثل "(#190) Error validating access token" (اختياري)

النشر على الصفحة مقلَّد أيضاً: لا يخرج أي طلب، وينجح بعد ثوانٍ بمعرّف وهمي (أو يفشل بـ publish_error).

مظهر الصفحة في رئيسية اللوحة: "cover" و"picture" (true = صورة مولَّدة يخدمها هذا الخادم على
/fake-facebook/pages/<id>/cover.png و picture.png، أو رابط صورة)، و"cover_offset_y" و"followers". صفحة
بلاها = صفحة بلا غلاف أو بلا صورة.

نشاط الصفحة (لكتابة الوصف بـ Gemini): يُقرأ من "category" و"about" للصفحة في ملف السيناريو (صفحة بلاهما
= فيسبوك لم يعطِ شيئاً). عامل الأوصاف يعمل داخل هذه العملية على القاعدة نفسها، والنموذج حقيقي:
Gemini بمفتاح GEMINI_API_KEY من .env.

محرك الفيديو: ffmpeg افتراضياً هنا (Remotion للسيرفر فقط). عامل الفيديو يُشغَّل منفصلاً (انظر STATUS.md).

قاعدة البيانات: نسخة مستقلة fake_facebook.db (تُنسخ من hojrat_bladi.db أول مرة) كي لا تختلط
صفحات التجربة ببيانات التطوير. احذف الملف للبدء من جديد.
"""
import argparse
import asyncio
import itertools
import json
import os
import shutil
import types
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
SCENARIO = Path(__file__).with_name("fake_facebook_scenario.json")
DB_COPY = ROOT / "fake_facebook.db"
FRONTEND_DONE_URL = "http://localhost:3000/connect/done/"


def _prepare_environment(port: int) -> None:
    """قبل استيراد التطبيق: القيم هنا تغلب .env (load_dotenv لا يستبدل متغيراً موجوداً)."""
    source = ROOT / "hojrat_bladi.db"
    if not DB_COPY.exists() and source.exists():
        shutil.copyfile(source, DB_COPY)
    os.environ["DATABASE_URL"] = f"sqlite:///{DB_COPY}"
    os.environ["BASE_URL"] = f"http://localhost:{port}"
    os.environ["FACEBOOK_SUCCESS_REDIRECT_URL"] = FRONTEND_DONE_URL
    os.environ.setdefault("META_APP_ID", "000000000000000")
    # الاتفاق: محلياً ffmpeg، وعلى السيرفر Remotion (غير مثبّت محلياً). setdefault: يبقى ممكناً تجاوزه صراحةً.
    os.environ.setdefault("CUSTOMER_VIDEO_ENGINE", "ffmpeg")


def _scenario() -> dict:
    return json.loads(SCENARIO.read_text(encoding="utf-8"))


async def _fake_graph_get(client, path, **params):
    scenario = _scenario()
    if path == "oauth/access_token":
        return {"access_token": "fake-user-token"}
    if path == "me":
        return {"id": "900000001", "name": "زبون تجريبي"}
    if path == "me/permissions":
        return {"data": [{"permission": scope, "status": "granted"} for scope in scenario["granted"]]}
    if path == "me/accounts":
        return {"data": scenario["pages"]}
    for page in scenario["pages"]:
        if page["id"] == path:
            return page
    raise RuntimeError(f"طلب Graph غير متوقع في خادم التقليد: {path}")


_fake_ids = itertools.count(1)


async def _fake_publish_call(client, method, path, *, token, data=None, params=None, files=None):
    """بديل graph_call داخل محوّل فيسبوك: نشر وهمي بلا أي طلب خارجي. غلاف الريل (thumb) يُحفظ في
    fake_facebook_last_cover.jpg بجانب قاعدة التقليد ليُرى بعد النشر."""
    from app.models import ReportKind
    from app.services.social.base import PublishError

    thumb = (files or {}).get("thumb")
    if thumb:
        (Path(__file__).resolve().parent.parent / "fake_facebook_last_cover.jpg").write_bytes(thumb[1])
    cover = f" | الغلاف: {len(thumb[1])} بايت" if thumb else " | بلا غلاف"
    print(f"[fake-facebook] نشر مقلَّد: {method} {path} | الرابط: {(data or {}).get('file_url') or (data or {}).get('url')}{cover}")
    await asyncio.sleep(4)
    error = _scenario().get("publish_error")
    if error:
        raise PublishError(ReportKind.TOKEN if "#190" in error else ReportKind.UNKNOWN, error)
    return {"id": f"99000000{next(_fake_ids):04d}"}


def _fake_page_activity(page_id, token):
    """بديل جلب نشاط الصفحة من فيسبوك: الحقول من ملف السيناريو."""
    from app.services import customer_captions

    page = next((item for item in _scenario()["pages"] if item["id"] == page_id), {})
    activity = customer_captions._page_activity(page)
    print(f"[fake-facebook] نشاط الصفحة {page_id}: {activity or '(لا شيء)'}")
    return activity


FAKE_ORIGIN = "http://localhost:8000"


def _fake_page_appearance(page_id, token):
    """بديل جلب الغلاف وصورة البروفيل من فيسبوك: من ملف السيناريو."""
    page = next((item for item in _scenario()["pages"] if item["id"] == page_id), None)
    if page is None:
        return None

    def image(kind):
        value = page.get(kind)
        if value is True:
            return f"{FAKE_ORIGIN}/fake-facebook/pages/{page_id}/{kind}.png"
        return value if isinstance(value, str) else None

    return {
        "picture_url": image("picture"), "cover_url": image("cover"),
        "cover_offset_y": int(page.get("cover_offset_y", 50)), "followers": page.get("followers"),
        "link": f"https://www.facebook.com/{page_id}",
    }


def _fake_page_image(page_id: str, kind: str) -> bytes:
    """صورة مولَّدة بلا أي ملف: غلاف بتدرّج ودوائر، أو بروفيل دائري الخلفية. اللون من رقم الصفحة."""
    import io

    from PIL import Image, ImageDraw

    seed = sum(map(ord, page_id))
    palettes = [((13, 27, 62), (20, 184, 166)), ((76, 29, 149), (236, 72, 153)), ((120, 53, 15), (245, 158, 11))]
    start, end = palettes[seed % len(palettes)]
    size = (1640, 624) if kind == "cover" else (320, 320)
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for x in range(size[0]):
        t = x / (size[0] - 1)
        draw.line([(x, 0), (x, size[1])], fill=tuple(int(a + (b - a) * t) for a, b in zip(start, end)))
    if kind == "cover":
        for i in range(9):
            r = 40 + (seed * (i + 3)) % 160
            cx, cy = (seed * (i + 7) * 97) % size[0], (seed * (i + 5) * 53) % size[1]
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255), width=3)
    else:
        draw.rounded_rectangle([90, 110, 230, 230], radius=18, fill=(255, 255, 255))   # حقيبة متجر بسيطة
        draw.arc([125, 70, 195, 150], 180, 360, fill=(255, 255, 255), width=10)
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def _start_description_worker() -> None:
    """عامل الأوصاف (app.jobs.generate_factory_descriptions) في خيط داخل هذه العملية وعلى قاعدتها."""
    import threading
    import time

    from app.core import job_lock
    from app.services import ollama_chat, post_drafts

    ollama_chat.recover_interrupted_description_jobs()

    def loop():
        while True:
            with job_lock.try_acquire() as acquired:
                job_id = ollama_chat.claim_next_description_job() if acquired else None
                if job_id is not None:
                    print(f"[fake-facebook] عامل الأوصاف: المهمة {job_id}")
                    ok = ollama_chat.process_description_job(job_id)
                    print(f"[fake-facebook] عامل الأوصاف: المهمة {job_id} {'نجحت' if ok else 'فشلت'}")
            if job_id is not None:
                post_drafts.advance_all()          # نص المسودة التالية (النشر الجماعي)
            if job_id is None:
                time.sleep(1.0)

    threading.Thread(target=loop, name="description-worker", daemon=True).start()


def _start_scheduled_publisher() -> None:
    """ما يفعله hadjret-publish.timer على السيرفر (كل دقيقة): ينشر المنشورات المعتمدة التي حان موعدها، بالنشر
    المقلَّد في هذه العملية. معه تقدّم المسودات المعتمدة (طلب فيديو ما اقترب موعده) كما يفعل عامل الفيديو."""
    import asyncio
    import threading
    import time

    from app.core.database import SessionLocal
    from app.services import post_drafts, post_publisher
    from app.services.social import build_publishers

    def loop():
        while True:
            time.sleep(30)
            try:
                post_drafts.advance_all()
                with SessionLocal() as db:
                    summary = asyncio.run(post_publisher.publish_due(db, publishers=build_publishers()))
                for outcome in summary.outcomes:
                    print(f"[fake-facebook] النشر المجدول: post={outcome.post_id} status={outcome.status}")
            except Exception as exc:      # لا يوقف الخادم
                print(f"[fake-facebook] النشر المجدول: خطأ {exc!r}")

    threading.Thread(target=loop, name="scheduled-publisher", daemon=True).start()


class _OfflineClient:
    """بديل httpx.AsyncClient داخل routers/auth: لا يخرج أي طلب إلى فيسبوك."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def delete(self, url, **kwargs):
        print(f"[fake-facebook] تُجوهل الطلب الخارجي: DELETE {url}")

    def __getattr__(self, name):
        raise RuntimeError(f"خادم التقليد لا يسمح بطلب خارجي: {name}")


def build_app():
    """التطبيق الحقيقي بعد استبدال كل ما يتصل بفيسبوك في مسار Facebook Login."""
    import httpx

    from app.main import app
    from app.routers import auth
    from fastapi import Response

    from app.services import customer_captions, page_appearance
    from app.services.social import facebook as facebook_publisher

    auth._graph_get = _fake_graph_get
    auth.httpx = types.SimpleNamespace(
        AsyncClient=_OfflineClient, HTTPError=httpx.HTTPError, Response=httpx.Response,
    )

    facebook_publisher.graph_call = _fake_publish_call
    customer_captions.fetch_page_activity = _fake_page_activity
    page_appearance.fetch_page_appearance = _fake_page_appearance

    @app.get("/fake-facebook/pages/{page_id}/{kind}.png", include_in_schema=False)
    def fake_page_image(page_id: str, kind: str):
        if kind not in ("cover", "picture"):
            return Response(status_code=404)
        return Response(_fake_page_image(page_id, kind), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"})
    _start_description_worker()
    _start_scheduled_publisher()

    @app.middleware("http")
    async def fake_dialog(request, call_next):
        response = await call_next(request)
        location = response.headers.get("location", "")
        if "facebook.com" in location and "dialog/oauth" in location:
            query = parse_qs(urlsplit(location).query)
            print(f"[fake-facebook] الأذونات المطلوبة من النافذة: {query['scope'][0]}")
            outcome = "error=access_denied" if _scenario().get("deny") else "code=fake"
            response.headers["location"] = f"/auth/facebook/callback?{outcome}&state={query['state'][0]}"
        return response

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="التطبيق محلياً مع فيسبوك مقلَّد")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    _prepare_environment(args.port)

    import uvicorn

    print(f"[fake-facebook] القاعدة: {DB_COPY.name} | السيناريو: {SCENARIO.name}")
    uvicorn.run(build_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
