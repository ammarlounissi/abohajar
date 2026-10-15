import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.routers import factories, posts, products, webhook
from app.services import errors

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate()      # يتوقف التشغيل فوراً إن نقص أي متغير في .env
    yield


app = FastAPI(title="Hojrat Bladi API", version="1.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)

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
app.include_router(products.router)
app.include_router(posts.router)
app.include_router(webhook.router)

app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}
