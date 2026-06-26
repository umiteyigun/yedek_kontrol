import os
import secrets
from typing import Optional

from fastapi import HTTPException, Request, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer

PANEL_USER = os.getenv("PANEL_USER", "admin")
PANEL_PASS = os.getenv("PANEL_PASS", "yedek2024")
SESSION_COOKIE = "yedek_panel_session"
SESSION_MAX_AGE = 60 * 60 * 8

_serializer = URLSafeSerializer(os.getenv("PANEL_SECRET", "change-me-in-production"), salt="yedek-panel")


def create_session_token(username: str) -> str:
    return _serializer.dumps({"user": username})


def read_session_token(token: str) -> Optional[str]:
    try:
        data = _serializer.loads(token)
        return data.get("user")
    except BadSignature:
        return None


def verify_login(username: str, password: str) -> bool:
    return secrets.compare_digest(username, PANEL_USER) and secrets.compare_digest(password, PANEL_PASS)


def get_current_user(request: Request) -> Optional[str]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return read_session_token(token)


def require_user(request: Request) -> str:
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
