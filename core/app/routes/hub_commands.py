"""Merkez hub toplu komut API — yalnizca proxy oturumu."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.central_proxy_auth import is_central_proxy_request, resolve_central_proxy_user
from app.services.hub_command_runner import list_commands, run_command

router = APIRouter(tags=["hub-commands"])


class HubCommandRunBody(BaseModel):
    command: str = Field(..., min_length=2, max_length=64)
    instance_id: str = Field(default="")
    parameters: dict[str, Any] = Field(default_factory=dict)


@router.get("/api/v1/hub/commands")
def hub_commands_catalog(request: Request):
    if not is_central_proxy_request(request):
        raise HTTPException(403, "Yalnizca merkez hub")
    if not resolve_central_proxy_user(request):
        raise HTTPException(403, "Merkez oturumu gerekli")
    return {"ok": True, "commands": list_commands()}


@router.post("/api/v1/hub/commands/run")
def hub_command_run(request: Request, body: HubCommandRunBody):
    if not is_central_proxy_request(request):
        raise HTTPException(403, "Yalnizca merkez hub")
    proxy_user = resolve_central_proxy_user(request)
    if not proxy_user:
        raise HTTPException(403, "Merkez oturumu gerekli")
    if body.command in {"oracle_password_change", "release_update"}:
        hub_role = str(proxy_user.get("hub_role") or "").lower()
        if hub_role != "superadmin":
            raise HTTPException(403, "Bu komut icin superadmin gerekli")
    store = request.app.state.store
    settings = store.get()
    try:
        result = run_command(
            settings,
            body.instance_id,
            body.command,
            body.parameters,
        )
        return JSONResponse(result)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
