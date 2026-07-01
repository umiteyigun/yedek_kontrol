import logging
import hashlib
import os
import secrets
import time
from typing import Any, Optional

from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.requests import Request

from app.config.ldap_config import ROLE_FULL
from app.services.ldap_auth import ldap_login
from app.services.central_proxy_auth import resolve_central_proxy_user
from app.services.permissions import can as perm_can, get_request_permissions, has_permission
from app.services.session_store import (
    PANEL_SESSION_TTL,
    TERMINAL_SESSION_TTL,
    SessionStore,
)

MASTER_USER = os.getenv("MASTER_USER") or os.getenv("PANEL_USER", "trtek-master")
MASTER_PASS = os.getenv("MASTER_PASS") or os.getenv("PANEL_PASS", "")
SESSION_COOKIE = "yedek_panel_session"
TERMINAL_SESSION_COOKIE = "yedek_terminal_session"
SESSION_MAX_AGE = PANEL_SESSION_TTL
TERMINAL_SESSION_MAX_AGE = TERMINAL_SESSION_TTL
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1").lower() in ("1", "true", "yes")

LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT", "8"))
LOGIN_RATE_WINDOW_SEC = int(os.getenv("LOGIN_RATE_WINDOW_SEC", "60"))

_serializer = URLSafeSerializer(os.getenv("PANEL_SECRET", "change-me-in-production"), salt="yedek-panel-v2")
_login_attempts: dict[str, list[float]] = {}
logger = logging.getLogger(__name__)


def _sign_session_id(session_id: str) -> str:
    return _serializer.dumps(session_id)


def unsign_session_id(token: str) -> Optional[str]:
    try:
        sid = _serializer.loads(token)
        return str(sid) if sid else None
    except BadSignature:
        return None


def ua_hash(user_agent: str | None) -> str:
    return hashlib.sha256((user_agent or "").encode()).hexdigest()[:16]


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return ""


def cookie_kwargs(*, max_age: int) -> dict[str, Any]:
    return {
        "httponly": True,
        "secure": COOKIE_SECURE,
        "samesite": "strict",
        "max_age": max_age,
        "path": "/",
    }


def _session_store(request: Request) -> SessionStore:
    store = getattr(request.app.state, "session_store", None)
    if store is None:
        raise RuntimeError("session_store baslatilmadi")
    return store


def create_panel_session(request: Request, username: str, auth_method: str, role: str) -> str:
    store = _session_store(request)
    sid = store.create_panel_session(
        user=username,
        role=role,
        auth=auth_method,
        ip=client_ip(request),
        ua_hash=ua_hash(request.headers.get("user-agent")),
    )
    return _sign_session_id(sid)


def create_terminal_session(request: Request, panel_session: dict[str, Any]) -> str | None:
    store = _session_store(request)
    parent_id = str(panel_session.get("sid") or "")
    if not parent_id:
        return None
    sid = store.create_terminal_session(
        parent_id=parent_id,
        user=str(panel_session.get("user") or ""),
        role=str(panel_session.get("role") or ""),
        auth=str(panel_session.get("auth") or ""),
        ip=client_ip(request),
        ua_hash=ua_hash(request.headers.get("user-agent")),
    )
    if not sid:
        return None
    return _sign_session_id(sid)


def resolve_panel_session(request: Request, token: str | None) -> Optional[dict[str, Any]]:
    sid = unsign_session_id(token or "")
    if not sid:
        return None
    store = _session_store(request)
    record = store.get_valid(
        sid,
        kind="panel",
        ip=client_ip(request),
        ua_hash=ua_hash(request.headers.get("user-agent")),
    )
    return record.to_public() if record else None


def resolve_terminal_session(
    store: SessionStore,
    token: str | None,
    *,
    ip: str,
    user_agent: str | None,
) -> Optional[dict[str, Any]]:
    sid = unsign_session_id(token or "")
    if not sid:
        return None
    record = store.get_valid(
        sid,
        kind="terminal",
        ip=ip,
        ua_hash=ua_hash(user_agent),
    )
    return record.to_public() if record else None


def revoke_panel_session(request: Request, session: dict[str, Any] | None) -> None:
    if not session:
        return
    store = _session_store(request)
    sid = str(session.get("sid") or "")
    if sid:
        store.revoke(sid)
        store.revoke_terminal_for_parent(sid)
    user = str(session.get("user") or "")
    if user:
        store.revoke_all_for_user(user)


def verify_master_login(username: str, password: str) -> bool:
    if not MASTER_PASS:
        return False
    return secrets.compare_digest(username, MASTER_USER) and secrets.compare_digest(password, MASTER_PASS)


def _login_rate_limited(ip: str) -> bool:
    now = time.time()
    window_start = now - LOGIN_RATE_WINDOW_SEC
    attempts = [t for t in _login_attempts.get(ip, []) if t >= window_start]
    _login_attempts[ip] = attempts
    return len(attempts) >= LOGIN_RATE_LIMIT


def _record_login_attempt(ip: str) -> None:
    now = time.time()
    window_start = now - LOGIN_RATE_WINDOW_SEC
    attempts = [t for t in _login_attempts.get(ip, []) if t >= window_start]
    attempts.append(now)
    _login_attempts[ip] = attempts


def is_login_rate_limited(request: Request) -> bool:
    return _login_rate_limited(client_ip(request))


def authenticate(request: Request, username: str, password: str) -> tuple[bool, str, str]:
    """Donus: (ok, auth_method, role)."""
    ip = client_ip(request)
    if _login_rate_limited(ip):
        return False, "", ""

    if verify_master_login(username, password):
        _login_attempts.pop(ip, None)
        return True, "master", ROLE_FULL

    store = getattr(request.app.state, "store", None)
    settings = store.get() if store else None
    auth_mode = settings.auth_mode if settings else "ldap_and_local"

    if auth_mode in ("local", "ldap_and_local"):
        local_store = getattr(request.app.state, "local_user_store", None)
        role_store = getattr(request.app.state, "local_role_store", None)
        if local_store:
            role = local_store.verify(username, password)
            if role and role_store and not role_store.role_exists(role):
                role = None
            if role:
                _login_attempts.pop(ip, None)
                return True, "local", role

    if auth_mode in ("ldap", "ldap_and_local") and settings:
        ok, role = ldap_login(username, password, settings)
        if ok and role:
            _login_attempts.pop(ip, None)
            return True, "ldap", role

    _record_login_attempt(ip)
    return False, "", ""


def cookie_kwargs_for_request(request: Request, *, max_age: int) -> dict[str, Any]:
    kwargs = cookie_kwargs(max_age=max_age)
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").lower()
    if proto != "https":
        kwargs["secure"] = False
    return kwargs


def _bootstrap_central_panel_session(request: Request) -> dict[str, Any] | None:
    claims = resolve_central_proxy_user(request)
    if not claims:
        return None
    store = _session_store(request)
    sid = store.create_panel_session(
        user=claims["username"],
        role=claims["panel_role"],
        auth="central",
        ip=client_ip(request),
        ua_hash=ua_hash(request.headers.get("user-agent")),
    )
    cookie_token = _sign_session_id(sid)
    session = store.get_valid(
        sid,
        kind="panel",
        ip=client_ip(request),
        ua_hash=ua_hash(request.headers.get("user-agent")),
    )
    if not session:
        return None
    logger.info(
        "Merkez proxy oturumu: user=%s role=%s hub_role=%s",
        claims["username"],
        claims["panel_role"],
        claims.get("hub_role", ""),
    )
    return {"session": session.to_public(), "cookie_token": cookie_token}


def get_session(request: Request) -> Optional[dict[str, Any]]:
    cached = getattr(request.state, "panel_session", None)
    if cached is not None:
        return cached

    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = resolve_panel_session(request, token)
        if session:
            request.state.panel_session = session
            return session

    if getattr(request.state, "_central_checked", False):
        request.state.panel_session = None
        return None
    request.state._central_checked = True

    boot = _bootstrap_central_panel_session(request)
    if boot:
        request.state.panel_session = boot["session"]
        request.state.central_session_cookie = boot["cookie_token"]
        return boot["session"]

    request.state.panel_session = None
    return None


def get_current_user(request: Request) -> Optional[str]:
    session = get_session(request)
    return session.get("user") if session else None


def get_current_role(request: Request) -> Optional[str]:
    session = get_session(request)
    return session.get("role") if session else None


def can(request: Request, module: str, action: str) -> bool:
    return perm_can(request, module, action)


def get_permissions(request: Request) -> dict[str, dict[str, bool]]:
    return get_request_permissions(request)


def can_manage_settings(request: Request) -> bool:
    """Geriye uyumluluk: ayarlar veya sistem modulu goruntuleme."""
    perms = get_request_permissions(request)
    return has_permission(perms, "settings", "view") or has_permission(perms, "system", "view")


def can_terminal_access(request: Request) -> bool:
    return can(request, "terminal", "view")


def is_full_access_session(session: dict[str, Any] | None) -> bool:
    if not session:
        return False
    auth = str(session.get("auth") or "")
    role = str(session.get("role") or "")
    if auth in ("master", "central", "ldap"):
        return role == ROLE_FULL
    return role == ROLE_FULL


def require_login(request: Request) -> dict[str, Any]:
    session = get_session(request)
    if not session:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return session


def require_full_access(request: Request) -> dict[str, Any]:
    session = require_login(request)
    if session.get("role") != ROLE_FULL:
        raise HTTPException(status_code=303, headers={"Location": "/?error=yetkisiz"})
    return session


def attach_central_session_cookie(request: Request, response: RedirectResponse) -> RedirectResponse:
    cookie = getattr(request.state, "central_session_cookie", None)
    if cookie:
        response.set_cookie(
            SESSION_COOKIE,
            cookie,
            **cookie_kwargs_for_request(request, max_age=SESSION_MAX_AGE),
        )
    return response


def login_redirect(request: Request | None = None) -> RedirectResponse:
    response = RedirectResponse(url="/login", status_code=303)
    if request is not None:
        return attach_central_session_cookie(request, response)
    return response


def settings_denied_redirect() -> RedirectResponse:
    return RedirectResponse(url="/?error=Bu+islem+icin+yetkiniz+yok", status_code=303)


def terminal_denied_redirect() -> RedirectResponse:
    return RedirectResponse(url="/?error=Terminal+icin+yetkiniz+yok", status_code=303)


def permission_denied_redirect(module: str = "") -> RedirectResponse:
    suffix = f"+({module})" if module else ""
    return RedirectResponse(url=f"/?error=Bu+islem+icin+yetkiniz+yok{suffix}", status_code=303)


def session_from_token(request: Request, token: str | None) -> Optional[dict[str, Any]]:
    return resolve_panel_session(request, token)

