"""Tablespace kontrol — admin only."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.auth import can, get_current_user, login_redirect, permission_denied_redirect
from app.routes.context import active_instance, page_context
from app.services.oracle_probe import is_instance_running
from app.services.oracle_tablespaces import (
    add_datafile,
    list_datafiles,
    list_tablespaces,
    suggest_next_datafile,
)
from app.web.templates_env import templates

router = APIRouter(tags=["tablespaces"])


class AddDatafileBody(BaseModel):
    file_path: str = Field(..., min_length=2)
    size_mb: int = Field(1024, ge=1, le=32767)
    auto_extend: bool = True
    next_mb: int = Field(100, ge=1, le=32767)
    max_size: str = "UNLIMITED"


def _resolve_instance(request: Request):
    settings = request.app.state.store.get()
    inst = active_instance(settings, request)
    if inst is None:
        for candidate in settings.instances:
            if is_instance_running(candidate.oracle_sid):
                inst = candidate
                break
        if inst is None:
            inst = settings.first_instance()
    return settings, inst


@router.get("/tablespace", response_class=HTMLResponse)
def tablespace_page(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "tablespaces", "view"):
        return permission_denied_redirect("tablespaces")

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
            "active_instance_id": inst.id if inst else "",
            "oracle_sid": inst.oracle_sid if inst else "",
            "error": error or ctx.get("error", ""),
            "view_mode": "datafiles" if selected else "overview",
            "datafile_suggest": suggest_next_datafile(selected, datafiles) if selected and datafiles else (
                suggest_next_datafile(selected, []) if selected else None
            ),
        }
    )
    return templates.TemplateResponse("tablespaces.html", ctx)


@router.get("/api/tablespaces")
def api_tablespaces(request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    if not can(request, "tablespaces", "view"):
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
    if not can(request, "tablespaces", "view"):
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


@router.get("/api/tablespaces/{tablespace}/datafiles/suggest")
def api_datafile_suggest(tablespace: str, request: Request):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    if not can(request, "tablespaces", "view"):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    _, inst = _resolve_instance(request)
    if not inst:
        return JSONResponse({"ok": False, "error": "instance yok"}, status_code=400)
    rows, error = list_datafiles(inst.oracle_sid, tablespace)
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=502)
    return {"ok": True, **suggest_next_datafile(tablespace, rows)}


@router.post("/api/tablespaces/{tablespace}/datafiles")
async def api_add_datafile(tablespace: str, request: Request, body: AddDatafileBody):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    if not can(request, "tablespaces", "add"):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)

    _, inst = _resolve_instance(request)
    if not inst:
        return JSONResponse({"ok": False, "error": "instance yok"}, status_code=400)

    ok, message = add_datafile(
        inst.oracle_sid,
        tablespace,
        body.file_path,
        body.size_mb,
        auto_extend=body.auto_extend,
        next_mb=body.next_mb,
        max_size=body.max_size,
    )
    if not ok:
        return JSONResponse({"ok": False, "error": message}, status_code=400)
    rows, error = list_datafiles(inst.oracle_sid, tablespace)
    if error:
        return {"ok": True, "message": message, "datafiles": [], "warning": error}
    return {
        "ok": True,
        "message": message,
        "tablespace": tablespace.upper(),
        "datafiles": [r.__dict__ for r in rows],
    }
