import logging
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.services import factories as factory_service

logger = logging.getLogger(__name__)
MAX_CONTEXT_TURNS = 5
SYSTEM_INSTRUCTION = (
    "أنت مساعد لمشروع حجرت بلادي، تساعد المصانع في تنظيم معلومات منتجاتها وكتابة أوصافها "
    "والرد على استفساراتها بوضوح وبالعربية. لا تخترع أسعارًا أو مواصفات غير مذكورة، "
    "واسأل عند نقص المعلومات."
)


def make_client(transport: Optional[httpx.AsyncBaseTransport] = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=120, transport=transport)


class OllamaUnavailableError(Exception):
    """Ollama did not return a usable response."""


def _conversation_messages(
    factory: models.Factory, history: list[models.FactoryChatMessage], prompt: str
) -> list[dict[str, str]]:
    profile = f"{SYSTEM_INSTRUCTION}\n\nبيانات المصنع:\nالاسم: {factory.name}\nالنشاط: {factory.activity or 'غير محدد'}"
    messages = [{"role": "system", "content": profile}]
    for message in history:
        messages.extend((
            {"role": "user", "content": message.prompt},
            {"role": "assistant", "content": message.response},
        ))
    messages.append({"role": "user", "content": prompt})
    return messages


async def generate_reply(messages: list[dict[str, str]]) -> str:
    try:
        async with make_client() as client:
            response = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "messages": messages,
                    "stream": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Ollama request failed: %s", type(exc).__name__)
        raise OllamaUnavailableError from exc
    except ValueError as exc:
        logger.warning("Ollama returned invalid JSON")
        raise OllamaUnavailableError from exc

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("message"), dict)
        or not isinstance(payload["message"].get("content"), str)
        or not payload["message"]["content"].strip()
    ):
        logger.warning("Ollama response is missing its generated message")
        raise OllamaUnavailableError
    return payload["message"]["content"]


async def chat(db: Session, factory_id: int, prompt: str) -> dict:
    factory = factory_service.get_factory(db, factory_id)
    history = list(reversed(
        db.query(models.FactoryChatMessage)
        .filter(models.FactoryChatMessage.factory_id == factory_id)
        .order_by(models.FactoryChatMessage.id.desc())
        .limit(MAX_CONTEXT_TURNS)
        .all()
    ))
    messages = _conversation_messages(factory, history, prompt)
    response = await generate_reply(messages)

    message = models.FactoryChatMessage(
        factory_id=factory_id,
        prompt=prompt,
        response=response,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return {
        "id": message.id,
        "factory_id": message.factory_id,
        "prompt": message.prompt,
        "response": message.response,
        "model": settings.OLLAMA_MODEL,
        "created_at": message.created_at,
    }
