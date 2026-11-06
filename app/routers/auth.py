"""مصادقة Facebook Login for Business وربط صفحات المستخدم."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.core.database import get_db

router = APIRouter(tags=["auth"])

_FACEBOOK_SCOPE = (
    "public_profile,pages_show_list,pages_messaging,pages_read_engagement,"
    "pages_manage_metadata,pages_manage_posts,pages_manage_engagement,"
    "business_management,catalog_management,read_page_mailboxes"
)
_STATE_MAX_AGE = 600


def _require_oauth_config() -> None:
    missing = [
        name for name in ("META_APP_ID", "META_APP_SECRET")
        if not getattr(settings, name)
    ]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"إعدادات Facebook OAuth ناقصة: {', '.join(missing)}",
        )


def _frontend_redirect(**params: str | int) -> RedirectResponse | None:
    """يعيد المستخدم إلى الواجهة مع نتيجة الربط في query. None إن لم يُضبط عنوان الواجهة."""
    base = settings.FACEBOOK_SUCCESS_REDIRECT_URL
    if not base:
        return None
    parts = urlsplit(base)
    query = "&".join(filter(None, [parts.query, urlencode(params)]))
    return RedirectResponse(urlunsplit(parts._replace(query=query)), status_code=303)


def _make_state() -> str:
    """ينشئ state عشوائيًا موقّعًا، ولا يحتاج إلى ذاكرة مشتركة بين العمال."""
    timestamp = str(int(time.time()))
    nonce = secrets.token_urlsafe(32)
    payload = f"{timestamp}.{nonce}"
    signature = hmac.new(
        settings.META_APP_SECRET.encode(), payload.encode(), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{payload}.{encoded_signature}"


def _validate_state(state: str) -> None:
    try:
        timestamp, nonce, encoded_signature = state.split(".", 2)
        payload = f"{timestamp}.{nonce}"
        signature = base64.urlsafe_b64decode(encoded_signature + "===")
        expected = hmac.new(
            settings.META_APP_SECRET.encode(), payload.encode(), hashlib.sha256
        ).digest()
        age = int(time.time()) - int(timestamp)
    except (ValueError, TypeError, base64.binascii.Error):
        raise HTTPException(status_code=400, detail="state غير صالح")
    if age < 0 or age > _STATE_MAX_AGE or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=400, detail="state منتهي أو غير صالح")


def _graph_error(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:500] or f"HTTP {response.status_code}"
    return str(body.get("error", {}).get("message", body))


async def _graph_get(client: httpx.AsyncClient, path: str, **params: str) -> dict[str, Any]:
    response = await client.get(f"{settings.graph_url}/{path.lstrip('/')}", params=params)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Facebook GET فشل: {_graph_error(response)}")
    try:
        body = response.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="Facebook أعاد استجابة غير صالحة")
    if isinstance(body, dict) and body.get("error"):
        raise HTTPException(status_code=502, detail=f"Facebook GET فشل: {body['error']}")
    return body


async def _graph_post(
    client: httpx.AsyncClient, path: str, *, data: dict[str, str]
) -> dict[str, Any]:
    response = await client.post(f"{settings.graph_url}/{path.lstrip('/')}", data=data)
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Facebook POST فشل: {_graph_error(response)}")
    try:
        body = response.json()
    except ValueError:
        return {"raw": response.text}
    if isinstance(body, dict) and body.get("error"):
        raise HTTPException(status_code=502, detail=f"Facebook POST فشل: {body['error']}")
    return body


def _request_value(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if isinstance(value, list):
        return value[0] if value else None
    return value if isinstance(value, str) else None


def _decode_signed_request(signed_request: str) -> dict[str, Any]:
    try:
        signature_text, payload_text = signed_request.split(".", 1)
        payload_bytes = base64.urlsafe_b64decode(payload_text + "===")
        expected = hmac.new(
            settings.META_APP_SECRET.encode(), payload_bytes, hashlib.sha256
        ).digest()
        signature = base64.urlsafe_b64decode(signature_text + "===")
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(payload_bytes)
    except (ValueError, TypeError, json.JSONDecodeError, base64.binascii.Error):
        raise HTTPException(status_code=400, detail="signed_request غير صالح")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="signed_request غير صالح")
    return payload


async def _read_callback_payload(request: Request) -> dict[str, Any]:
    body = await request.body()
    if not body:
        return {}
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            value = json.loads(body)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="JSON غير صالح")
        return value if isinstance(value, dict) else {}
    return {key: values[0] for key, values in parse_qs(body.decode()).items() if values}


@router.get("/facebook/login")
async def facebook_login():
    """يبدأ تسجيل الدخول العام إلى Facebook."""
    _require_oauth_config()
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": settings.FACEBOOK_REDIRECT_URI,
        "scope": _FACEBOOK_SCOPE,
        "response_type": "code",
        "state": _make_state(),
    }
    return RedirectResponse(
        f"https://www.facebook.com/{settings.GRAPH_API_VERSION}/dialog/oauth?{urlencode(params)}"
    )


@router.get("/facebook/callback")
async def facebook_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    """يبدّل code بتوكن المستخدم ويربط صفحاته ويشترك في Webhooks."""
    _require_oauth_config()
    if error:
        redirect = _frontend_redirect(facebook="error", reason=error)
        if redirect:
            return redirect
        raise HTTPException(status_code=400, detail=f"Facebook رفض التفويض: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="code وstate مطلوبان")
    _validate_state(state)

    async with httpx.AsyncClient(timeout=30) as client:
        token_data = await _graph_get(
            client,
            "oauth/access_token",
            client_id=settings.META_APP_ID,
            client_secret=settings.META_APP_SECRET,
            redirect_uri=settings.FACEBOOK_REDIRECT_URI,
            code=code,
        )
        user_token = token_data.get("access_token")
        if not user_token:
            raise HTTPException(status_code=502, detail="Facebook لم يُرجع user_token")
        owner = await _graph_get(
            client, "me", fields="id,name", access_token=user_token
        )
        pages_data = await _graph_get(
            client,
            "me/accounts",
            fields="id,name,access_token",
            access_token=user_token,
        )
        pages = pages_data.get("data", [])
        linked_pages = []
        subscription_errors = []
        for page in pages:
            page_id = str(page.get("id", "")).strip()
            page_name = str(page.get("name", "")).strip()
            page_token = page.get("access_token")
            if not page_id or not page_token:
                subscription_errors.append({"page_id": page_id, "error": "بيانات الصفحة ناقصة"})
                continue
            try:
                await _graph_post(
                    client,
                    f"{page_id}/subscribed_apps",
                    data={
                        "subscribed_fields": "feed,messages,messaging_postbacks,messaging_optins,message_deliveries",
                        "access_token": page_token,
                    },
                )
            except HTTPException as exc:
                subscription_errors.append({"page_id": page_id, "error": exc.detail})
                continue

            channel = (
                db.query(models.SocialChannel)
                .filter(models.SocialChannel.page_id == page_id)
                .first()
            )
            if channel is None:
                channel = models.SocialChannel(
                    platform="facebook",
                    name=page_name or page_id,
                    description=f"صفحة Facebook: {page_name or page_id}",
                    remote_id=page_id,
                    page_id=page_id,
                    page_name=page_name or page_id,
                    page_access_token=page_token,
                    owner_fbid=str(owner.get("id", "")) or None,
                    owner_name=owner.get("name"),
                    factory_id=None,
                    is_active=True,
                    bot_enabled=True,
                )
                db.add(channel)
            else:
                channel.name = page_name or channel.name
                channel.description = channel.description or f"صفحة Facebook: {page_name or page_id}"
                channel.remote_id = page_id
                channel.page_name = page_name or channel.page_name
                channel.page_access_token = page_token
                channel.owner_fbid = str(owner.get("id", "")) or channel.owner_fbid
                channel.owner_name = owner.get("name") or channel.owner_name
                channel.is_active = True
                channel.bot_enabled = True
            linked_pages.append({"page_id": page_id, "page_name": page_name})

    db.commit()
    redirect = _frontend_redirect(
        facebook="connected",
        pages=len(linked_pages),
        errors=len(subscription_errors),
    )
    if redirect:
        return redirect
    return {
        "status": "success",
        "owner_fbid": owner.get("id"),
        "owner_name": owner.get("name"),
        "pages": linked_pages,
        "subscription_errors": subscription_errors,
    }


@router.post("/facebook/deauthorize")
async def facebook_deauthorize(request: Request, db: Session = Depends(get_db)):
    """يحذف صفحات المستخدم عند إلغاء تصريح Facebook."""
    _require_oauth_config()
    payload = await _read_callback_payload(request)
    signed_request = _request_value(payload, "signed_request")
    data = _decode_signed_request(signed_request) if signed_request else payload
    owner_fbid = _request_value(data, "user_id") or _request_value(data, "user_fbid")
    if not owner_fbid and isinstance(data.get("user"), dict):
        owner_fbid = str(data["user"].get("id", "")) or None
    if not owner_fbid:
        raise HTTPException(status_code=400, detail="لم يُعثر على user_id")
    deleted = (
        db.query(models.SocialChannel)
        .filter(models.SocialChannel.owner_fbid == owner_fbid)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"status": "success", "owner_fbid": owner_fbid, "deleted_pages": deleted}


@router.post("/facebook/delete")
async def facebook_delete(request: Request, db: Session = Depends(get_db)):
    """يحذف بيانات صفحات المستخدم ويرجع confirmation_code."""
    payload = await _read_callback_payload(request)
    signed_request = _request_value(payload, "signed_request")
    data = _decode_signed_request(signed_request) if signed_request else payload
    owner_fbid = _request_value(data, "user_id") or _request_value(data, "user_fbid")
    if not owner_fbid and isinstance(data.get("user"), dict):
        owner_fbid = str(data["user"].get("id", "")) or None
    if not owner_fbid:
        raise HTTPException(status_code=400, detail="لم يُعثر على user_id")
    deleted = (
        db.query(models.SocialChannel)
        .filter(models.SocialChannel.owner_fbid == owner_fbid)
        .delete(synchronize_session=False)
    )
    db.commit()
    return {
        "url": f"{settings.BASE_URL}/auth/facebook/delete",
        "confirmation_code": secrets.token_urlsafe(24),
        "owner_fbid": owner_fbid,
        "deleted_pages": deleted,
    }


@router.get("/facebook/status")
def facebook_status(db: Session = Depends(get_db)):
    """يرجع الصفحات المربوطة دون كشف Page Access Token."""
    channels = (
        db.query(models.SocialChannel)
        .filter(models.SocialChannel.platform == "facebook")
        .order_by(models.SocialChannel.id)
        .all()
    )
    return {
        "pages": [
            {
                "page_id": channel.page_id or channel.remote_id,
                "page_name": channel.page_name or channel.name,
                "description": channel.description,
                "owner_fbid": channel.owner_fbid,
                "owner_name": channel.owner_name,
                "is_active": channel.is_active,
                "bot_enabled": channel.bot_enabled,
                "has_page_access_token": bool(channel.page_access_token),
                "created_at": channel.created_at,
            }
            for channel in channels
        ]
    }
