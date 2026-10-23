import logging
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.services import factories as factory_service

logger = logging.getLogger(__name__)


def make_client(transport: Optional[httpx.AsyncBaseTransport] = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=120, transport=transport)


class OllamaUnavailableError(Exception):
    """Ollama did not return a usable response."""


def _conversation_prompt(history: list[models.FactoryChatMessage], prompt: str) -> str:
    messages = []
    for message in history:
        messages.extend((
            f"المستخدم: {message.prompt}",
            f"المساعد: {message.response}",
        ))
    messages.extend((f"المستخدم: {prompt}", "المساعد:"))
    return "\n".join(messages)


async def generate_reply(prompt: str) -> str:
    try:
        async with make_client() as client:
            response = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": settings.OLLAMA_MODEL,
                    "prompt": prompt,
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
        or not isinstance(payload.get("response"), str)
        or not payload["response"].strip()
    ):
        logger.warning("Ollama response is missing its generated text")
        raise OllamaUnavailableError
    return payload["response"]


async def chat(db: Session, factory_id: int, prompt: str) -> dict:
    factory_service.get_factory(db, factory_id)
    history = (
        db.query(models.FactoryChatMessage)
        .filter(models.FactoryChatMessage.factory_id == factory_id)
        .order_by(models.FactoryChatMessage.id)
        .all()
    )
    response = await generate_reply(_conversation_prompt(history, prompt))

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
