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

قاعدة البيانات: نسخة مستقلة fake_facebook.db (تُنسخ من hojrat_bladi.db أول مرة) كي لا تختلط
صفحات التجربة ببيانات التطوير. احذف الملف للبدء من جديد.
"""
import argparse
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

    auth._graph_get = _fake_graph_get
    auth.httpx = types.SimpleNamespace(
        AsyncClient=_OfflineClient, HTTPError=httpx.HTTPError, Response=httpx.Response,
    )

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
