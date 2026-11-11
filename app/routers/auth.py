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

from app import schemas
from app.core.config import settings
from app.core.database import get_db
from app.services import facebook_services, sessions, social_channels, users
from app.services.errors import ConflictError, DuplicateError, InvalidInputError, NotFoundError, ServiceError

router = APIRouter(tags=["auth"])

# الأذونات المطلوبة من فيسبوك تتبع الخدمة (services/facebook_services.py)، ولا تُطلب كلها دفعة واحدة.
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


def _make_state(user_id: int | None = None, service: str = "") -> str:
    """state عشوائي موقّع بصيغة time.nonce.user_id.service.sig ولا يحتاج ذاكرة مشتركة بين العمال.
    user_id=0 يعني أن المتصفح لا يملك جلسة (الدخول بفيسبوك مباشرة)، وservice فارغ يعني دخولاً بلا خدمة."""
    payload = f"{int(time.time())}.{secrets.token_urlsafe(32)}.{user_id or 0}.{service}"
    signature = hmac.new(
        settings.META_APP_SECRET.encode(), payload.encode(), hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"{payload}.{encoded_signature}"


def _validate_state(state: str) -> tuple[int | None, str, str]:
    """يتحقق من التوقيع والعمر، ويرجع (user_id أو None، nonce، مفتاح الخدمة أو "")."""
    try:
        timestamp, nonce, uid, service, encoded_signature = state.split(".")
        payload = f"{timestamp}.{nonce}.{uid}.{service}"
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
    return (user_id or None), nonce, service


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


def _service_or_404(key: str) -> facebook_services.FacebookService:
    try:
        return facebook_services.get_service(key)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/facebook/login")
async def facebook_login(request: Request, service: str | None = None):
    """يبدأ الدخول بفيسبوك. مع ?service=<مفتاح> تُطلب أذونات تلك الخدمة وحدها وتُربط لها صفحة واحدة؛
    وبدونه دخول بالهوية فقط بلا أي صفحة. إن كان المتصفح مسجِّلاً دخوله بالبريد يُربط فيسبوك بحسابه نفسه."""
    _require_oauth_config()
    chosen = _service_or_404(service) if service else None
    session_user = sessions.read_token(request.cookies.get(settings.SESSION_COOKIE_NAME))
    state = _make_state(session_user, chosen.key if chosen else "")
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": settings.FACEBOOK_REDIRECT_URI,
        "scope": ",".join(chosen.scopes if chosen else facebook_services.LOGIN_SCOPES),
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


def _outcome(
    status: str, *, service: str = "", reason: str = "", detail: str = "", fallback_status: int = 200,
) -> Response:
    """نتيجة callback: تحويل إلى الواجهة (?facebook=<status>&service=..&reason=..)،
    أو JSON بالمحتوى نفسه إن لم يُضبط عنوان الواجهة."""
    params = {"facebook": status}
    if service:
        params["service"] = service
    if reason:
        params["reason"] = reason
    redirect = _frontend_redirect(**params)
    if redirect:
        return redirect
    body = {"status": status, "service": service or None, "reason": reason or None}
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=fallback_status)


async def _granted_scopes(client: httpx.AsyncClient, user_token: str) -> set[str]:
    """الأذونات التي منحها المستخدم فعلاً: نافذة فيسبوك تسمح له برفض بعضها."""
    data = await _graph_get(client, "me/permissions", access_token=user_token)
    return {
        str(item.get("permission")) for item in data.get("data", [])
        if isinstance(item, dict) and item.get("status") == "granted"
    }


def _connect_service_page(
    db: Session, *, service: facebook_services.FacebookService, user, page_id: str, page_name: str,
    page_token: str, owner_name: str | None,
) -> bool:
    """يربط الصفحة بالمستخدم ويجعلها صفحة الخدمة الوحيدة. False إن كانت مربوطة بمستخدم آخر. لا commit."""
    channel = social_channels.link_facebook_page(
        db, user_id=user.id, page_id=page_id, page_name=page_name, page_token=page_token,
        owner_fbid=user.facebook_id, owner_name=owner_name,
    )
    if channel is None:
        return False
    facebook_services.activate(db, user_id=user.id, service=service.key, channel=channel)
    return True


@router.get("/facebook/callback")
async def facebook_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    """يبدّل code بتوكن المستخدم، يحدّد المستخدم (جلسته أو حساب فيسبوك) ويفتح له جلسة.
    إن بدأ الدخول لخدمة: يتحقق من أذوناتها ثم يربط لها صفحة واحدة؛ فإن منح المستخدم أكثر من صفحة
    لا يُربط شيء حتى يختار واحدة (POST /auth/facebook/services/<مفتاح>/select)."""
    _require_oauth_config()
    if error:
        service_key = ""
        if state:
            try:
                service_key = _validate_state(state)[2]
            except HTTPException:
                pass
        return _outcome(
            "error", service=service_key, reason=error,
            detail=f"Facebook رفض التفويض: {error}", fallback_status=400,
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="code وstate مطلوبان")
    state_user_id, nonce, service_key = _validate_state(state)
    if not hmac.compare_digest(request.cookies.get(_OAUTH_COOKIE, ""), nonce):
        raise HTTPException(status_code=400, detail="state لا يخص هذا المتصفح")
    service = _service_or_404(service_key) if service_key else None

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
            return _outcome("error", service=service_key, reason=reason, detail=str(exc), fallback_status=409)

        if service is None:
            response = _outcome("connected")
        else:
            response = await _callback_for_service(
                client, db, service=service, user=user, user_token=user_token, owner_name=owner.get("name"),
            )

    db.commit()
    _set_session(response, user.id)
    response.delete_cookie(_OAUTH_COOKIE, path="/auth/facebook")
    return response


async def _callback_for_service(
    client: httpx.AsyncClient, db: Session, *, service: facebook_services.FacebookService, user,
    user_token: str, owner_name: str | None,
) -> Response:
    missing = service.missing_scopes(await _granted_scopes(client, user_token))
    if missing:
        return _outcome(
            "error", service=service.key, reason="missing_permissions",
            detail=f"أذونات لم تُمنح: {', '.join(missing)}", fallback_status=403,
        )
    pages_data = await _graph_get(
        client, "me/accounts", fields="id,name,access_token", limit="100", access_token=user_token,
    )
    pages = [
        {"id": str(page.get("id", "")).strip(), "name": str(page.get("name", "")).strip(),
         "access_token": page.get("access_token")}
        for page in pages_data.get("data", []) if isinstance(page, dict)
    ]
    pages = [page for page in pages if page["id"] and page["access_token"]]
    if not pages:
        return _outcome(
            "error", service=service.key, reason="no_pages",
            detail="لم يمنح المستخدم الإذن لأي صفحة", fallback_status=409,
        )
    if len(pages) > 1:
        facebook_services.begin_selection(
            db, user_id=user.id, service=service.key, pages=pages, user_token=user_token, owner_name=owner_name,
        )
        return _outcome("select", service=service.key)
    page = pages[0]
    linked = _connect_service_page(
        db, service=service, user=user, page_id=page["id"], page_name=page["name"],
        page_token=page["access_token"], owner_name=owner_name,
    )
    if not linked:
        return _outcome(
            "error", service=service.key, reason="page_in_use",
            detail="الصفحة مربوطة بمستخدم آخر", fallback_status=409,
        )
    return _outcome("connected", service=service.key)


@router.get("/facebook/services/{service_key}", response_model=schemas.FacebookServiceStatus)
def facebook_service_status(
    service_key: str, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """حالة الخدمة للمستخدم الحالي: صفحتها المربوطة (واحدة) والصفحات المنتظِرة اختياره إن وُجدت."""
    service = _service_or_404(service_key)
    status = facebook_services.describe(db, user.id, service)
    db.commit()   # describe يمحو الاختيار المعلّق إن انتهت مهلته
    return status


@router.post("/facebook/services/{service_key}/select", response_model=schemas.FacebookServiceStatus)
async def facebook_service_select(
    service_key: str, body: schemas.FacebookPageSelection,
    user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """يختار المستخدم صفحة واحدة من التي منحها في نافذة فيسبوك: تُربط هي وحدها ويُمحى التوكن المؤقت."""
    service = _service_or_404(service_key)
    try:
        choice = facebook_services.pending_choice(db, user.id, service.key, body.page_id.strip())
    except ConflictError as exc:
        db.commit()   # مُحي الاختيار المنتهي
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    async with httpx.AsyncClient(timeout=30) as client:
        page = await _graph_get(
            client, choice.page_id, fields="id,name,access_token", access_token=choice.user_token,
        )
    page_token = page.get("access_token")
    if not page_token:
        raise HTTPException(status_code=502, detail="Facebook لم يُرجع توكن الصفحة")
    linked = _connect_service_page(
        db, service=service, user=user, page_id=choice.page_id,
        page_name=str(page.get("name", "")).strip(), page_token=page_token, owner_name=choice.owner_name,
    )
    if not linked:
        raise HTTPException(status_code=409, detail="الصفحة مربوطة بمستخدم آخر")
    db.commit()
    return facebook_services.describe(db, user.id, service)


@router.delete("/facebook/services/{service_key}/selection", response_model=schemas.FacebookServiceStatus)
def facebook_service_cancel_selection(
    service_key: str, user=Depends(get_current_user), db: Session = Depends(get_db),
):
    """يلغي اختيار الصفحة المعلّق ويمحو التوكن المؤقت دون ربط أي صفحة."""
    service = _service_or_404(service_key)
    facebook_services.cancel_selection(db, user.id, service.key)
    db.commit()
    return facebook_services.describe(db, user.id, service)


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
