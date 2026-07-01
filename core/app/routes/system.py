import json
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from app.web.templates_env import templates

from app.auth import can, get_current_user, login_redirect, permission_denied_redirect
from app.config.ldap_config import LDAP_SETTINGS_DEFAULTS, ROLE_LIMITED
from app.config.models import YedekSettings
from app.routes.context import page_context
from app.services.ldap_auth import test_ldap_connection
from app.services.permissions import (
    ACTION_LABELS,
    MODULES,
    MODULE_ORDER,
    ROLE_DEFAULTS,
    parse_permissions_from_form,
)
from app.services.server_info import clear_server_info_cache
from app.services.server_time import collect_host_clock, list_host_timezones, set_host_clock, set_host_timezone

router = APIRouter(tags=["system"])
AUTH_MODE_LABELS = {
    "ldap": "Yalnizca LDAP",
    "local": "Yalnizca yerel kullanicilar",
    "ldap_and_local": "LDAP + yerel kullanicilar",
}


def _field(form, name: str, default: str = "") -> str:
    value = form.get(name)
    return str(value).strip() if value is not None else default


def _parse_auth_form(form, current: YedekSettings) -> dict[str, Any]:
    payload = current.model_dump()
    auth_mode = _field(form, "auth_mode", current.auth_mode)
    if auth_mode not in AUTH_MODE_LABELS:
        auth_mode = current.auth_mode
    payload.update(
        {
            "auth_mode": auth_mode,
            "ldap_enabled": form.get("ldap_enabled") == "1",
            "ldap_host": _field(form, "ldap_host", current.ldap_host),
            "ldap_port": int(_field(form, "ldap_port", str(current.ldap_port)) or current.ldap_port),
            "ldap_use_ssl": form.get("ldap_use_ssl") == "1",
            "ldap_base_dn": _field(form, "ldap_base_dn", current.ldap_base_dn),
            "ldap_user_dn_template": _field(form, "ldap_user_dn_template", current.ldap_user_dn_template),
            "ldap_group_base": _field(form, "ldap_group_base", current.ldap_group_base),
            "ldap_groups_full": _field(form, "ldap_groups_full", current.ldap_groups_full),
            "ldap_groups_limited": _field(form, "ldap_groups_limited", current.ldap_groups_limited),
            "ldap_bind_dn": _field(form, "ldap_bind_dn", current.ldap_bind_dn),
            "ldap_search_filter": _field(form, "ldap_search_filter", current.ldap_search_filter),
        }
    )
    bind_pass = _field(form, "ldap_bind_password")
    if bind_pass:
        payload["ldap_bind_password"] = bind_pass
    return payload


@router.get("/sistem", response_class=HTMLResponse)
def system_page(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "system", "view"):
        return permission_denied_redirect("system")

    store = request.app.state.store
    local_store = request.app.state.local_user_store
    role_store = request.app.state.local_role_store
    user_counts = local_store.role_counts()
    roles = role_store.list_roles(user_counts)
    role_labels = {row.role_id: row.label for row in roles}
    settings = store.get()
    ctx = page_context(
        request,
        settings,
        message=request.query_params.get("message", ""),
        error=request.query_params.get("error", ""),
    )
    ctx.update(
        {
            "auth_mode_labels": AUTH_MODE_LABELS,
            "ldap_defaults": LDAP_SETTINGS_DEFAULTS,
            "local_roles": roles,
            "local_users": local_store.list_users(role_labels),
            "ldap_has_bind_password": bool(settings.ldap_bind_password),
            "timezone_options": list_host_timezones(),
            "host_clock": collect_host_clock(),
            "permission_modules": MODULES,
            "permission_module_order": MODULE_ORDER,
            "permission_action_labels": ACTION_LABELS,
            "role_perm_presets_json": json.dumps(ROLE_DEFAULTS, ensure_ascii=False),
            "local_roles_json": json.dumps(
                [
                    {
                        "role_id": r.role_id,
                        "label": r.label,
                        "builtin": r.builtin,
                        "permissions": r.permissions,
                    }
                    for r in roles
                ],
                ensure_ascii=False,
            ),
            "can_system_edit": can(request, "system", "edit"),
            "can_system_add": can(request, "system", "add"),
            "can_system_delete": can(request, "system", "delete"),
        }
    )
    return templates.TemplateResponse("sistem.html", ctx)


@router.post("/sistem/kaydet")
async def system_save(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "system", "edit"):
        return permission_denied_redirect("system")

    form = await request.form()
    store = request.app.state.store
    current = store.get()
    try:
        parsed = _parse_auth_form(form, current)
        updated = YedekSettings.model_validate(parsed)
        store.replace(updated.model_dump())
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(url=f"/sistem?error={quote_plus(str(exc))}", status_code=303)

    return RedirectResponse(url="/sistem?message=Kimlik+dogrulama+ayarlari+kaydedildi", status_code=303)


@router.post("/sistem/ldap/test")
async def system_ldap_test(request: Request):
    if not get_current_user(request):
        return login_redirect(request)
    if not can(request, "system", "edit"):
        return JSONResponse({"ok": False, "message": "Yetkisiz"}, status_code=403)

    form = await request.form()
    store = request.app.state.store
    current = store.get()
    try:
        draft = YedekSettings.model_validate(_parse_auth_form(form, current))
        ok, message = test_ldap_connection(draft)
        return JSONResponse({"ok": ok, "message": message})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)


@router.post("/sistem/yerel/ekle")
async def system_local_add(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "system", "add"):
        return permission_denied_redirect("system")

    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))
    role = str(form.get("role", ROLE_LIMITED))

    local_store = request.app.state.local_user_store
    role_store = request.app.state.local_role_store
    try:
        if not role_store.role_exists(role):
            raise ValueError("Gecersiz rol")
        local_store.add_user(username, password, role)
    except ValueError as exc:
        return RedirectResponse(url=f"/sistem?error={quote_plus(str(exc))}", status_code=303)

    return RedirectResponse(url="/sistem?message=Yerel+kullanici+eklendi", status_code=303)


@router.post("/sistem/yerel/{username}/guncelle")
async def system_local_update(request: Request, username: str):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "system", "edit"):
        return permission_denied_redirect("system")

    form = await request.form()
    role = str(form.get("role", ""))
    enabled = str(form.get("enabled", ""))
    password = str(form.get("password", ""))

    local_store = request.app.state.local_user_store
    role_store = request.app.state.local_role_store
    try:
        if role and not role_store.role_exists(role):
            raise ValueError("Gecersiz rol")
        local_store.update_user(
            username,
            role=role or None,
            enabled=(enabled == "1") if enabled else None,
            password=password or None,
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/sistem?error={quote_plus(str(exc))}", status_code=303)

    return RedirectResponse(url="/sistem?message=Kullanici+guncellendi", status_code=303)


@router.post("/sistem/yerel/{username}/sil")
async def system_local_delete(request: Request, username: str):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "system", "delete"):
        return permission_denied_redirect("system")

    local_store = request.app.state.local_user_store
    try:
        local_store.delete_user(username)
    except ValueError as exc:
        return RedirectResponse(url=f"/sistem?error={quote_plus(str(exc))}", status_code=303)

    return RedirectResponse(url="/sistem?message=Kullanici+silindi", status_code=303)


@router.post("/sistem/rol/ekle")
async def system_role_add(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "system", "add"):
        return permission_denied_redirect("system")

    form = await request.form()
    role_id = str(form.get("role_id", ""))
    label = str(form.get("label", ""))
    permissions = parse_permissions_from_form(form)
    role_store = request.app.state.local_role_store
    try:
        role_store.add_role(role_id, label, permissions)
    except ValueError as exc:
        return RedirectResponse(url=f"/sistem?error={quote_plus(str(exc))}", status_code=303)
    return RedirectResponse(url="/sistem?message=Rol+eklendi", status_code=303)


@router.post("/sistem/rol/{role_id}/guncelle")
async def system_role_update(request: Request, role_id: str):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "system", "edit"):
        return permission_denied_redirect("system")

    form = await request.form()
    label = str(form.get("label", ""))
    permissions = parse_permissions_from_form(form)
    role_store = request.app.state.local_role_store
    try:
        role_store.update_role(role_id, label=label or None, permissions=permissions)
    except ValueError as exc:
        return RedirectResponse(url=f"/sistem?error={quote_plus(str(exc))}", status_code=303)
    return RedirectResponse(url="/sistem?message=Rol+guncellendi", status_code=303)


@router.post("/sistem/rol/{role_id}/sil")
async def system_role_delete(request: Request, role_id: str):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "system", "delete"):
        return permission_denied_redirect("system")

    local_store = request.app.state.local_user_store
    role_store = request.app.state.local_role_store
    in_use = local_store.count_users_with_role(role_id) > 0
    try:
        role_store.delete_role(role_id, in_use=in_use)
    except ValueError as exc:
        return RedirectResponse(url=f"/sistem?error={quote_plus(str(exc))}", status_code=303)
    return RedirectResponse(url="/sistem?message=Rol+silindi", status_code=303)


@router.post("/sistem/saat-dilimi")
async def system_set_timezone(request: Request, server_timezone: str = Form(...)):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "system", "edit"):
        return permission_denied_redirect("system")

    store = request.app.state.store
    current = store.get()
    ok, message, _clock = set_host_timezone(server_timezone)
    if not ok:
        return RedirectResponse(url=f"/sistem?error={quote_plus(message)}", status_code=303)

    payload = current.model_dump()
    payload["server_timezone"] = server_timezone.strip()
    store.replace(payload)
    clear_server_info_cache()
    return RedirectResponse(url=f"/sistem?message={quote_plus(message)}", status_code=303)


@router.post("/sistem/saat")
async def system_set_clock(
    request: Request,
    clock_date: str = Form(...),
    clock_time: str = Form(...),
    server_timezone: str = Form(...),
    return_to: str = Form("/"),
):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "system", "edit"):
        return permission_denied_redirect("system")

    store = request.app.state.store
    current = store.get()
    ok, message, _clock = set_host_clock(clock_date, clock_time, server_timezone)
    if not ok:
        target = return_to if return_to.startswith("/") else "/"
        return RedirectResponse(url=f"{target}?error={quote_plus(message)}", status_code=303)

    payload = current.model_dump()
    payload["server_timezone"] = server_timezone.strip()
    store.replace(payload)
    clear_server_info_cache()

    target = return_to if return_to.startswith("/") else "/"
    return RedirectResponse(url=f"{target}?message={quote_plus(message)}", status_code=303)
