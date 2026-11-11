import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.security import require_docs_login
from app.routers import (
    auth, chat, customer_workspace, factories, posts, products, public_media, social_channels, webhook,
)
from app.services import errors

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()      # يتوقف التشغيل فوراً إن نقص أي متغير في .env
    yield


# مسارات التوثيق الافتراضية معطّلة؛ تُعرَّف أسفل الملف خلف require_docs_login.
app = FastAPI(
    title="IkhlasDZ Bot", version="1.2.0", lifespan=lifespan,
    docs_url=None, redoc_url=None, openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,   # كوكي الجلسة يُرسل من الواجهة؛ ALLOWED_ORIGINS صريحة فلا يُسمح بـ *
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

# بلا includeSubDomains: الترويسة تصدر من نطاق الـ API فقط ولا تُلزم نطاقات أخرى بـ https.
_SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}


# يُسجَّل بعد CORS ليغلّفه، فتحمل ردود preflight الترويسات أيضاً.
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response

# ترجمة أخطاء طبقة الخدمات إلى رموز HTTP في مكان واحد (الخدمات لا تعرف HTTP).
# الرسائل عربية ومناسبة للعرض، والمسار الناتج مطابق لما كانت المسارات ترفعه سابقاً.
_ERROR_STATUS = {
    errors.NotFoundError: 404,
    errors.DuplicateError: 400,
    errors.ConflictError: 409,
    errors.InvalidInputError: 422,
}


@app.exception_handler(errors.ServiceError)
async def service_error_handler(request: Request, exc: errors.ServiceError):
    code = next((c for cls, c in _ERROR_STATUS.items() if isinstance(exc, cls)), 400)
    return JSONResponse(status_code=code, content={"detail": str(exc)})


app.include_router(factories.router)
app.include_router(chat.router)
app.include_router(products.router)
app.include_router(posts.router)
app.include_router(public_media.router)
app.include_router(social_channels.router)
app.include_router(customer_workspace.router)
app.include_router(webhook.router)
app.include_router(auth.router, prefix="/auth")


@app.get("/health")
def health():
    return {"status": "ok"}


_docs_only = [Depends(require_docs_login)]


@app.get("/openapi.json", include_in_schema=False, dependencies=_docs_only)
def openapi_schema():
    return app.openapi()


@app.get("/docs", include_in_schema=False, dependencies=_docs_only)
def swagger_docs():
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")


@app.get("/redoc", include_in_schema=False, dependencies=_docs_only)
def redoc_docs():
    return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")
