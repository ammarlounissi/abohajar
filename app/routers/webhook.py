import hmac
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.core.security import verify_signature
from app.services.webhook_handler import process_payload

router = APIRouter(tags=["webhook"])


@router.get("/webhook")
async def verify_webhook(
    mode: Optional[str] = Query(None, alias="hub.mode"),
    token: Optional[str] = Query(None, alias="hub.verify_token"),
    challenge: Optional[str] = Query(None, alias="hub.challenge"),
):
    if (
        mode == "subscribe"
        and challenge
        and token
        and hmac.compare_digest(token.encode(), settings.VERIFY_TOKEN.encode())
    ):
        return PlainTextResponse(challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook", dependencies=[Depends(verify_signature)])
async def receive_webhook(request: Request, background: BackgroundTasks):
    """نرد على ميتا فوراً بـ 200 ونعالج الرسالة في الخلفية، حتى لا تعيد إرسالها."""
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    background.add_task(process_payload, payload)
    return {"status": "received"}
