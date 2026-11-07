"""جلسة المستخدم (بريد وكلمة سر أو فيسبوك) وربط صفحاته عبر Facebook Login for Business."""
import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.services import sessions, social_channels, users
from app.services.errors import ConflictError, DuplicateError, NotFoundError, ServiceError

router = APIRouter(tags=["auth"])

_FACEBOOK_SCOPE = (
    "public_profile,email,pages_show_list,pages_messaging,pages_read_engagement,"
    "pages_manage_metadata,pages_manage_posts,pages_manage_engagement,"
    "business_management,catalog_management,read_page_mailboxes"
)
_STATE_MAX_AGE = 600
_OAUTH_COOKIE = "hb_oauth"   # يربط state بالمتصفح الذي بدأ الدخول (يمنع تسجيل دخول مزوّر)


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


def _make_state(user_id: int | None = None) -> str:
    """state عشوائي موقّع بصيغة time.nonce.user_id.sig ولا يحتاج ذاكرة مشتركة بين العمال.
    user_id=0 يعني أن المتصفح لا يملك جلسة (الدخول بفيسبوك مباشرة)."""
    payload = f"{int(time.time())}.{secrets.token_urlsafe(32)}.{user_id or 0}"
    signature = hmac.new(
        settings.META_APP_SECRET.encode(), payload.encode(), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{payload}.{encoded_signature}"


def _validate_state(state: str) -> tuple[int | None, str]:
    """يتحقق من التوقيع والعمر، ويرجع (user_id أو None، nonce)."""
    try:
        timestamp, nonce, uid, encoded_signature = state.split(".", 3)
        payload = f"{timestamp}.{nonce}.{uid}"
        signature = base64.urlsafe_b64decode(encoded_signature + "===")
        expected = hmac.new(
            settings.META_APP_SECRET.encode(), payload.encode(), hashlib.sha256
        ).digest()
        age = int(time.time()) - int(timestamp)
        user_id = int(uid)
    except (ValueError, TypeError, base64.binascii.Error):
        raise HTTPException(status_code=400, detail="state غير صالح")
    if age < 0 or age > _STATE_MAX_AGE or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=400, detail="state منتهي أو غير صالح")
    return (user_id or None), nonce


# ───────────── الجلسة ─────────────

def _set_session(response: Response, user_id: int) -> None:
    response.set_cookie(
        settings.SESSION_COOKIE_NAME, sessions.make_token(user_id),
        max_age=settings.SESSION_MAX_AGE, httponly=True, samesite="lax",
        secure=settings.session_cookie_secure, path="/",
    )


def _user_json(user) -> dict[str, Any]:
    return {
        "id": user.id, "email": user.email, "name": user.name, "role": user.role,
        "has_facebook": bool(user.facebook_id), "has_password": bool(user.password_hash),
    }


def get_current_user(request: Request, db: Session = Depends(get_db)):
    """المستخدم صاحب كوكي الجلسة، وإلا 401. تستعملها أي مسارات تتطلب تسجيل الدخول."""
    user_id = sessions.read_token(request.cookies.get(settings.SESSION_COOKIE_NAME))
    if user_id is None:
        raise HTTPException(status_code=401, detail="سجّل الدخول أولاً")
    try:
        return users.get_active_user(db, user_id)
    except NotFoundError:
        raise HTTPException(status_code=401, detail="سجّل الدخول أولاً")


class _Credentials(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=256)   # سقف يمنع إجهاد scrypt بنصوص ضخمة


class _Registration(_Credentials):
    name: str = Field(default="", max_length=255)


@router.post("/register")
def register(body: _Registration, db: Session = Depends(get_db)):
    user = users.create_user_with_password(db, body.email, body.password, name=body.name)
    response = JSONResponse({"user": _user_json(user)}, status_code=201)
    _set_session(response, user.id)
    return response


@router.post("/login")
def login(body: _Credentials, db: Session = Depends(get_db)):
    try:
        user = users.login_with_password(db, body.email, body.password)
    except ServiceError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    response = JSONResponse({"user": _user_json(user)})
    _set_session(response, user.id)
    return response


@router.post("/logout")
def logout():
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    return response


@router.get("/me")
def me(user=Depends(get_current_user)):
    return {"user": _user_json(user)}


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
async def facebook_login(request: Request):
    """يبدأ الدخول/الربط بفيسبوك. إن كان المتصفح مسجِّلاً دخوله بالبريد تُربط الصفحات بحسابه نفسه."""
    _require_oauth_config()
    session_user = sessions.read_token(request.cookies.get(settings.SESSION_COOKIE_NAME))
    state = _make_state(session_user)
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": settings.FACEBOOK_REDIRECT_URI,
        "scope": _FACEBOOK_SCOPE,
        "response_type": "code",
        "state": state,
    }
    host = f"{settings.FACEBOOK_LOGIN_LOCALE}.facebook.com" if settings.FACEBOOK_LOGIN_LOCALE else "www.facebook.com"
    response = RedirectResponse(
        f"https://{host}/{settings.GRAPH_API_VERSION}/dialog/oauth?{urlencode(params)}"
    )
    response.set_cookie(
        _OAUTH_COOKIE, state.split(".")[1], max_age=_STATE_MAX_AGE, httponly=True,
        samesite="lax", secure=settings.session_cookie_secure, path="/auth/facebook",
    )
    return response


_REASONS = {DuplicateError: "facebook_in_use", ConflictError: "account_conflict"}


@router.get("/facebook/callback")
async def facebook_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    """يبدّل code بتوكن المستخدم، يحدّد المستخدم (جلسته أو حساب فيسبوك)، يربط صفحاته به ويفتح له جلسة."""
    _require_oauth_config()
    if error:
        redirect = _frontend_redirect(facebook="error", reason=error)
        if redirect:
            return redirect
        raise HTTPException(status_code=400, detail=f"Facebook رفض التفويض: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="code وstate مطلوبان")
    state_user_id, nonce = _validate_state(state)
    if not hmac.compare_digest(request.cookies.get(_OAUTH_COOKIE, ""), nonce):
        raise HTTPException(status_code=400, detail="state لا يخص هذا المتصفح")

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
            client, "me", fields="id,name,email", access_token=user_token
        )
        owner_fbid = str(owner.get("id", "")).strip()

        try:
            if state_user_id:
                user = users.attach_facebook(db, users.get_active_user(db, state_user_id), owner_fbid)
            else:
                user = users.login_with_facebook(db, owner_fbid, owner.get("name", ""), owner.get("email"))
        except ServiceError as exc:
            reason = next((r for cls, r in _REASONS.items() if isinstance(exc, cls)), "account")
            redirect = _frontend_redirect(facebook="error", reason=reason)
            if redirect:
                return redirect
            raise HTTPException(status_code=409, detail=str(exc))

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

            channel = social_channels.link_facebook_page(
                db, user_id=user.id, page_id=page_id, page_name=page_name, page_token=page_token,
                owner_fbid=owner_fbid or None, owner_name=owner.get("name"),
            )
            if channel is None:
                subscription_errors.append({"page_id": page_id, "error": "الصفحة مربوطة بمستخدم آخر"})
                continue
            linked_pages.append({"page_id": page_id, "page_name": page_name})

    db.commit()
    response = _frontend_redirect(
        facebook="connected",
        pages=len(linked_pages),
        errors=len(subscription_errors),
    ) or JSONResponse({
        "status": "success",
        "owner_fbid": owner.get("id"),
        "owner_name": owner.get("name"),
        "pages": linked_pages,
        "subscription_errors": subscription_errors,
    })
    _set_session(response, user.id)
    response.delete_cookie(_OAUTH_COOKIE, path="/auth/facebook")
    return response


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
    deleted = social_channels.delete_by_owner_fbid(db, owner_fbid)
    users.remove_facebook_identity(db, owner_fbid)   # يحفظ (commit) ويحذف بيانات الدخول أيضاً
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
    deleted = social_channels.delete_by_owner_fbid(db, owner_fbid)
    users.remove_facebook_identity(db, owner_fbid)   # يحفظ (commit) ويحذف بيانات الدخول أيضاً
    return {
        "url": f"{settings.BASE_URL}/auth/facebook/delete",
        "confirmation_code": secrets.token_urlsafe(24),
        "owner_fbid": owner_fbid,
        "deleted_pages": deleted,
    }


@router.delete("/facebook/pages/{page_id}")
async def facebook_delete_page(
    page_id: str, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """يحذف صفحة يملكها المستخدم المسجَّل دخوله فقط، ويلغي اشتراك التطبيق فيها عند فيسبوك."""
    try:
        channel = social_channels.get_user_facebook_page(db, user.id, page_id)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="الصفحة غير موجودة")
    page_token = channel.page_access_token
    if page_token:
        # محاولة بأفضل جهد: فشلها (توكن منتهٍ مثلاً) لا يمنع حذف بياناتنا
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.delete(
                    f"{settings.graph_url}/{page_id}/subscribed_apps",
                    params={"access_token": page_token},
                )
        except httpx.HTTPError:
            pass
    result = social_channels.remove_user_facebook_page(db, channel)
    return {"status": "ok", "page_id": page_id, "result": result}


@router.get("/facebook/status")
def facebook_status(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """الصفحات التي ربطها المستخدم المسجَّل دخوله فقط، دون كشف Page Access Token."""
    channels = social_channels.list_user_facebook_pages(db, user.id)
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
