"""استدعاء Graph API مع تحويل كل فشل إلى PublishError مصنَّف."""
from typing import Optional

import httpx

from app.core.config import settings
from app.models import ReportKind
from app.services.social.base import PublishError, classify_graph_error


def make_client(timeout: float = 60.0, transport: Optional[httpx.AsyncBaseTransport] = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=transport)


async def graph_call(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    token: str,
    data: Optional[dict] = None,
    params: Optional[dict] = None,
) -> dict:
    url = f"{settings.graph_url}/{path.lstrip('/')}"
    try:
        response = await client.request(
            method, url, data=data, params=params, headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError as exc:      # انتهاء المهلة أو انقطاع الاتصال
        raise PublishError(ReportKind.NETWORK, f"{type(exc).__name__}: {exc}") from exc

    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None

    if response.status_code >= 400 or error:
        error = error if isinstance(error, dict) else {}
        kind = classify_graph_error(response.status_code, error)
        message = error.get("message") or response.text[:500] or f"http_{response.status_code}"
        code = error.get("code")
        if code is not None:
            sub = error.get("error_subcode")
            message = f"(#{code}{f'/{sub}' if sub else ''}) {message}"
        raise PublishError(kind, message)

    return body if isinstance(body, dict) else {}
