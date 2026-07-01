from pathlib import Path

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse
from app.web.templates_env import templates

from app.auth import (
    TERMINAL_SESSION_COOKIE,
    TERMINAL_SESSION_MAX_AGE,
    can_terminal_access,
    cookie_kwargs,
    create_terminal_session,
    get_current_user,
    get_session,
    login_redirect,
    terminal_denied_redirect,
)
from app.services.terminal_bridge import authorize_terminal_ws, run_terminal_session

router = APIRouter(tags=["terminal"])


@router.get("/terminal", response_class=HTMLResponse)
def terminal_page(request: Request):
    user = get_current_user(request)
    if not user:
        return login_redirect()
    if not can_terminal_access(request):
        return terminal_denied_redirect()

    panel_session = get_session(request)
    if not panel_session:
        return terminal_denied_redirect()

    terminal_token = create_terminal_session(request, panel_session)
    if not terminal_token:
        return terminal_denied_redirect()

    response = templates.TemplateResponse(
        "terminal.html",
        {
            "request": request,
            "user": user,
            "can_settings": True,
            "role_label": "Tam Yetki",
        },
    )
    response.set_cookie(
        TERMINAL_SESSION_COOKIE,
        terminal_token,
        **cookie_kwargs(max_age=TERMINAL_SESSION_MAX_AGE),
    )
    return response


@router.websocket("/ws/terminal")
async def terminal_ws(websocket: WebSocket):
    auth = authorize_terminal_ws(websocket)
    if not auth:
        await websocket.accept()
        await websocket.close(code=4403, reason="Yetkisiz")
        return
    await run_terminal_session(websocket, auth)
