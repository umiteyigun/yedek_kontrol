"""Tablespace kontrol — admin only."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.auth import can_manage_settings, get_current_user, login_redirect, settings_denied_redirect
from app.routes.context import active_instance, page_context
from app.services.oracle_tablespaces import list_datafiles, list_tablespaces
from app.web.templates_env import templates

router = APIRouter(tags=["tablespaces"])


def _resolve_instance(request: Request):
    settings = request.app.state.store.get()
    inst = active_instance(settings, request) or settings.first_instance()
    return settings, inst


@router.get("/tablespace", response_class=HTMLResponse)
def tablespace_page(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    settings, inst = _resolve_instance(request)
    ctx = page_context(request, settings)
    rows: list = []
    error = ""
    selected = (request.query_params.get("ts") or "").strip().upper()
    datafiles: list = []

    if inst and inst.oracle_sid:
        rows, error = list_tablespaces(inst.oracle_sid)
        if selected and not error:
            datafiles, df_err = list_datafiles(inst.oracle_sid, selected)
            if df_err:
                error = df_err
    else:
        error = "Aktif Oracle instance bulunamadi"

    ctx.update(
        {
            "tablespaces": rows,
            "datafiles": datafiles,
            "selected_tablespace": selected,
            "oracle_sid": inst.oracle_sid if inst else "",
            "error": error or ctx.get("error", ""),
            "view_mode": "datafiles" if selected and datafiles else "overview",
        }
    )
    return templates.TemplateResponse("tablespaces.html", ctx)


@router.get("/api/tablespaces")
def api_tablespaces(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    if not can_manage_settings(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    _, inst = _resolve_instance(request)
    if not inst:
        return JSONResponse({"ok": False, "error": "instance yok"}, status_code=400)
    rows, error = list_tablespaces(inst.oracle_sid)
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=502)
    return {
        "ok": True,
        "oracle_sid": inst.oracle_sid,
        "tablespaces": [r.__dict__ for r in rows],
    }


@router.get("/api/tablespaces/{tablespace}/datafiles")
def api_datafiles(tablespace: str, request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    if not can_manage_settings(request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    _, inst = _resolve_instance(request)
    if not inst:
        return JSONResponse({"ok": False, "error": "instance yok"}, status_code=400)
    rows, error = list_datafiles(inst.oracle_sid, tablespace)
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=502)
    return {
        "ok": True,
        "tablespace": tablespace.upper(),
        "datafiles": [r.__dict__ for r in rows],
    }
