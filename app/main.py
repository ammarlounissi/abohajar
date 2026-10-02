import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.routers import factories, products, webhook

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

app.include_router(factories.router)
app.include_router(products.router)
app.include_router(webhook.router)

app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")


@app.get("/health")
def health():
    return {"status": "ok"}
