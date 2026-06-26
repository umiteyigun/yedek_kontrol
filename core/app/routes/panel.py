import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from app.web.templates_env import templates

from app.auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    TERMINAL_SESSION_COOKIE,
    attach_central_session_cookie,
    authenticate,
    can_manage_settings,
    cookie_kwargs,
    cookie_kwargs_for_request,
    create_panel_session,
    get_current_user,
    get_session,
    is_login_rate_limited,
    login_redirect,
    revoke_panel_session,
    settings_denied_redirect,
)
from app.services.central_proxy_auth import is_central_proxy_request, resolve_central_proxy_user
from app.config.constants import ORACLE_DIRECTORY_NAME
from app.config.models import (
    BackupScheduleRule,
    InstanceSettings,
    WEEKDAY_LABELS,
    YedekSettings,
    normalize_upper_ascii,
    slugify,
)
from app.routes.context import page_context
from app.services.oracle_probe import (
    OracleProbeResult,
    apply_probe_to_settings_dict,
    list_instance_schemas,
    probe_all_instances,
    probe_instance,
)
from app.services.oracle_discovery import sync_instances_from_oratab
from app.services.ftp_client import browse_directory, delete_files

router = APIRouter(tags=["panel"])


def _parse_settings_form(form, current: YedekSettings) -> dict[str, Any]:
    """Tum ayarlari formdan oku (geriye uyumluluk)."""
    payload = _parse_global_form(form, current)
    payload["instances"] = _parse_all_instances_form(form, current)
    return payload


def _parse_all_instances_form(form, current: YedekSettings) -> list[dict[str, Any]]:
    instances: list[dict[str, Any]] = []
    for inst in current.instances:
        prefix = f"inst_{inst.id}_"
        if not any(form.get(f"{prefix}{name}") is not None for name in ("hastane", "enabled", "localftpip")):
            instances.append(inst.model_dump())
            continue
        instances.append(_parse_instance_form(form, inst, current.yedek_dir, prefix=prefix))
    return instances


def _parse_instance_form(
    form,
    inst: InstanceSettings,
    yedek_dir: str,
    *,
    prefix: str = "",
) -> dict[str, Any]:
    """Tek kurumun duzenlenebilir alanlarini formdan oku."""

    def field(name: str, default: str = "") -> str:
        value = form.get(f"{prefix}{name}")
        return str(value).strip() if value is not None else default

    ftp_pass = field("localftppass")
    protect_pass = field("backup_protect_pass")
    retention_raw = field("retention_days", str(inst.retention_days or 2))
    protect_mode = field("backup_protect_mode", inst.backup_protect_mode or "gzip").lower()
    if protect_mode not in {"gzip", "oracle", "zip"}:
        protect_mode = "gzip"
    split_size_raw = field("backup_split_size_mb", str(inst.backup_split_size_mb or 2048))
    return {
        "id": inst.id,
        "enabled": form.get(f"{prefix}enabled") == "1",
        "label": normalize_upper_ascii(field("label", inst.label)),
        "hastane": field("hastane", inst.hastane),
        "il": normalize_upper_ascii(field("il", inst.il)),
        "password": "",
        "schemas": field("schemas", inst.schemas),
        "kurumkodu": field("kurumkodu", inst.kurumkodu),
        "directory": ORACLE_DIRECTORY_NAME,
        "directorydizini": inst.effective_directorydizini(yedek_dir),
        "oracle_sid": inst.oracle_sid,
        "yedek_kodu": field("yedek_kodu", inst.yedek_kodu),
        "guid_key": field("guid_key", inst.guid_key),
        "localftpip": field("localftpip", inst.localftpip),
        "localftpuser": field("localftpuser", inst.localftpuser),
        "localftppass": ftp_pass if ftp_pass else inst.localftppass,
        "localftpdir": field("localftpdir", inst.localftpdir or "/") or "/",
        "retention_days": max(1, int(retention_raw or inst.retention_days or 2)),
        "backup_protect_mode": protect_mode,
        "backup_protect_pass": protect_pass if protect_pass else inst.backup_protect_pass,
        "backup_split_enabled": form.get(f"{prefix}backup_split_enabled") == "1",
        "backup_split_size_mb": max(512, min(8192, int(split_size_raw or inst.backup_split_size_mb or 2048))),
        "schedules": [rule.model_dump() for rule in inst.schedules],
    }


def _parse_global_form(form, current: YedekSettings) -> dict[str, Any]:
    """Disk guvenligi ve merkezi bildirim alanlarini formdan oku."""

    def field(name: str, default: str = "") -> str:
        value = form.get(name)
        return str(value).strip() if value is not None else default

    payload = current.model_dump()
    payload.update(
        {
            "remote_api_url": field("remote_api_url", current.remote_api_url),
            "mail_notify": form.get("mail_notify") == "1",
            "backup_disk_max_pct": int(
                field("backup_disk_max_pct", str(current.backup_disk_max_pct)) or current.backup_disk_max_pct
            ),
            "backup_disk_min_free_gb": float(
                field("backup_disk_min_free_gb", str(current.backup_disk_min_free_gb))
                or current.backup_disk_min_free_gb
            ),
            "backup_disk_reserve_gb": float(
                field("backup_disk_reserve_gb", str(current.backup_disk_reserve_gb))
                or current.backup_disk_reserve_gb
            ),
            "backup_size_margin_pct": int(
                field("backup_size_margin_pct", str(current.backup_size_margin_pct))
                or current.backup_size_margin_pct
            ),
            "panel_log_retention_days": int(
                field("panel_log_retention_days", str(current.panel_log_retention_days))
                or current.panel_log_retention_days
            ),
        }
    )
    return payload


def _validate_instance(inst: dict[str, Any]) -> list[str]:
    """Tek aktif kurum icin zorunlu alanlari kontrol et."""
    if not inst.get("enabled"):
        return []
    label = str(inst.get("label") or inst.get("hastane") or inst.get("id") or "kurum")
    errors: list[str] = []
    if not str(inst.get("hastane", "")).strip():
        errors.append(f"{label}: hastane adi zorunlu")
    if not str(inst.get("localftpip", "")).strip():
        errors.append(f"{label}: uzak FTP IP zorunlu")
    if not str(inst.get("localftpuser", "")).strip():
        errors.append(f"{label}: uzak FTP kullanici zorunlu")
    if not str(inst.get("localftppass", "")).strip():
        errors.append(f"{label}: uzak FTP sifre zorunlu")
    mode = str(inst.get("backup_protect_mode", "gzip")).lower()
    if mode in {"oracle", "zip"} and not str(inst.get("backup_protect_pass", "")).strip():
        errors.append(f"{label}: yedek koruma sifresi zorunlu ({mode})")
    return errors


def _validate_parsed_settings(parsed: dict[str, Any]) -> list[str]:
    """Aktif kurumlar icin zorunlu alanlari kontrol et."""
    errors: list[str] = []
    for inst in parsed.get("instances", []):
        errors.extend(_validate_instance(inst))
    return errors


def _replace_instance_in_settings(
    current: YedekSettings,
    instance_id: str,
    updated_row: dict[str, Any],
) -> dict[str, Any]:
    payload = current.model_dump()
    instances: list[dict[str, Any]] = []
    found = False
    for inst in current.instances:
        if inst.id == instance_id:
            found = True
            instances.append(updated_row)
        else:
            instances.append(inst.model_dump())
    if not found:
        raise ValueError("Instance bulunamadi")
    payload["instances"] = instances
    return payload


def _merge_oracle_probes(settings_dict: dict[str, Any]) -> tuple[dict[str, Any], dict[str, OracleProbeResult], list[str]]:
    probes, errors = probe_all_instances(settings_dict)
    merged = apply_probe_to_settings_dict(settings_dict, probes)
    return merged, probes, errors


def _enrich_schedule_rules(schedules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for row in schedules:
        rule = BackupScheduleRule.model_validate(row)
        item = rule.model_dump()
        item["summary"] = rule.summary()
        item["backup_type_label"] = rule.backup_type_label()
        enriched.append(item)
    return enriched


def _unique_schedule_id(existing: set[str], backup_type: str, time_value: str, day: int | None) -> str:
    base = slugify(f"{backup_type}-{time_value}-{day if day is not None else 'daily'}")
    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _parse_schedule_form(form) -> BackupScheduleRule:
    backup_type = str(form.get("backup_type", "GUNLUK")).strip().upper()
    if backup_type not in {"GUNLUK", "HAFTALIK"}:
        raise ValueError("Yedek tipi GUNLUK veya HAFTALIK olmali")

    time_value = str(form.get("time", "02:00")).strip()
    day_raw = str(form.get("day_of_week", "")).strip()
    day_of_week: int | None = int(day_raw) if day_raw != "" else None
    enabled = form.get("enabled") == "1"
    label = str(form.get("label", "")).strip()
    rule_id = str(form.get("rule_id", "")).strip()

    if not rule_id:
        rule_id = slugify(f"{backup_type}-{time_value}")

    return BackupScheduleRule(
        id=rule_id,
        enabled=enabled,
        backup_type=backup_type,  # type: ignore[arg-type]
        time=time_value,
        day_of_week=day_of_week,
        label=label,
    )


def _update_instance_schedules(
    settings: YedekSettings,
    instance_id: str,
    updater,
) -> YedekSettings:
    found = False
    instances: list[dict[str, Any]] = []
    for inst in settings.instances:
        row = inst.model_dump()
        if inst.id == instance_id:
            found = True
            row["schedules"] = updater(list(row.get("schedules", [])))
        instances.append(row)
    if not found:
        raise ValueError("Instance bulunamadi")
    payload = settings.model_dump()
    payload["instances"] = instances
    return YedekSettings.model_validate(payload)


def _settings_context(
    request: Request,
    settings: YedekSettings | dict[str, Any],
    *,
    probes: dict[str, OracleProbeResult] | None = None,
    probe_errors: list[str] | None = None,
    message: str = "",
    error: str = "",
) -> dict[str, Any]:
    if isinstance(settings, YedekSettings):
        settings_dict = settings.model_dump()
    else:
        settings_dict = settings
        settings = YedekSettings.model_validate(settings_dict)

    ctx = page_context(request, settings, message=message, error=error)
    probe_map = probes or {}
    probe_errs = probe_errors or []

    enriched_instances = []
    for inst in ctx["instances"]:
        row = dict(inst)
        probe = probe_map.get(inst["id"])
        if probe:
            row["oracle_probe_ok"] = probe.ok
            row["oracle_probe_error"] = probe.error
            if probe.ok:
                row["directory"] = ORACLE_DIRECTORY_NAME
                row["directorydizini"] = probe.directorydizini or f"{probe.yedek_dir}/"
        else:
            row["oracle_probe_ok"] = None
            row["oracle_probe_error"] = ""
        row["schedules"] = _enrich_schedule_rules(row.get("schedules", []))
        enriched_instances.append(row)

    ctx["instances"] = enriched_instances
    ctx["settings"] = settings_dict
    ctx["weekday_labels"] = list(WEEKDAY_LABELS)
    ctx["oracle_directory"] = ORACLE_DIRECTORY_NAME
    ctx["oracle_probe_errors"] = probe_errs
    ctx["oracle_probe_ok"] = not probe_errs and bool(probe_map)
    return ctx


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_current_user(request):
        return attach_central_session_cookie(request, RedirectResponse(url="/", status_code=303))
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if is_central_proxy_request(request) and resolve_central_proxy_user(request):
        response = RedirectResponse(url="/", status_code=303)
        session = get_session(request)
        cookie = getattr(request.state, "central_session_cookie", None)
        if cookie:
            response.set_cookie(
                SESSION_COOKIE,
                cookie,
                **cookie_kwargs_for_request(request, max_age=SESSION_MAX_AGE),
            )
        return response
    if is_login_rate_limited(request):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Cok fazla basarisiz deneme. Lutfen 1 dakika bekleyip tekrar deneyin.",
            },
            status_code=429,
        )
    ok, auth_method, role = authenticate(request, username, password)
    if not ok:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Giris basarisiz. LDAP, yerel kullanici veya master hesabi gerekli.",
            },
            status_code=401,
        )
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_panel_session(request, username, auth_method, role),
        **cookie_kwargs_for_request(request, max_age=SESSION_MAX_AGE),
    )
    return response


@router.get("/logout")
def logout(request: Request):
    session = get_session(request)
    revoke_panel_session(request, session)
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(TERMINAL_SESSION_COOKIE, path="/")
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not get_current_user(request):
        return login_redirect(request)
    settings = request.app.state.store.get()
    return templates.TemplateResponse("dashboard.html", page_context(request, settings))


@router.get("/ayarlar", response_class=HTMLResponse)
def settings_page(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()
    settings = request.app.state.store.get()
    merged, probes, probe_errors = _merge_oracle_probes(settings.model_dump())
    display_settings = YedekSettings.model_validate(merged)
    return templates.TemplateResponse(
        "settings.html",
        _settings_context(
            request,
            display_settings,
            probes=probes,
            probe_errors=probe_errors,
            message=request.query_params.get("message", ""),
            error=request.query_params.get("error", ""),
        ),
    )


@router.post("/ayarlar/oracle-sync")
async def settings_oracle_sync(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    store = request.app.state.store
    current = store.get()
    payload, discovered = sync_instances_from_oratab(current.model_dump())
    merged, probes, probe_errors = _merge_oracle_probes(payload)
    if discovered or any(p.ok for p in probes.values()):
        saved = store.replace(merged)
        message = "Oracle bilgileri senkronize edildi (TRTEK directory)."
        if discovered:
            message = f"oratab'tan yeni instance eklendi: {', '.join(discovered)}. " + message
        if probe_errors:
            message += f" Uyarilar: {'; '.join(probe_errors)}"
        return templates.TemplateResponse(
            "settings.html",
            _settings_context(request, saved, probes=probes, probe_errors=probe_errors, message=message),
        )
    return templates.TemplateResponse(
        "settings.html",
        _settings_context(
            request,
            current,
            probes=probes,
            probe_errors=probe_errors,
            error="Oracle senkronizasyonu basarisiz: " + ("; ".join(probe_errors) or "bilinmeyen hata"),
        ),
        status_code=500,
    )


@router.post("/ayarlar/oracle-discover")
async def settings_oracle_discover(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    store = request.app.state.store
    current = store.get()
    updated, added = sync_instances_from_oratab(current.model_dump())
    if added:
        saved = store.replace(updated)
        merged, probes, probe_errors = _merge_oracle_probes(saved.model_dump())
        if any(p.ok for p in probes.values()):
            saved = store.replace(merged)
        return templates.TemplateResponse(
            "settings.html",
            _settings_context(
                request,
                saved,
                probes=probes,
                probe_errors=probe_errors,
                message=f"oratab tarandi, yeni instance eklendi: {', '.join(added)}",
            ),
        )
    return templates.TemplateResponse(
        "settings.html",
        _settings_context(request, current, message="oratab tarandi, yeni instance bulunamadi"),
    )


@router.post("/ayarlar/instance/{instance_id}/ftp/browse")
async def settings_ftp_browse(request: Request, instance_id: str):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Oturum gerekli"}, status_code=401)
    if not can_manage_settings(request):
        return JSONResponse({"ok": False, "error": "Ayar yetkisi gerekli"}, status_code=403)

    store = request.app.state.store
    current = store.get()
    target = current.get_instance(instance_id)
    if not target:
        return JSONResponse({"ok": False, "error": "Instance bulunamadi"}, status_code=404)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}

    host = str(body.get("host", "")).strip() or target.localftpip
    user = str(body.get("user", "")).strip() or target.localftpuser
    password = str(body.get("password", "")) or target.localftppass
    path = str(body.get("path", "")).strip() or (target.localftpdir or "/")

    if not host:
        return JSONResponse({"ok": False, "error": "FTP sunucu adresi bos"}, status_code=400)
    if not user:
        return JSONResponse({"ok": False, "error": "FTP kullanici adi bos"}, status_code=400)

    try:
        result = browse_directory(
            host,
            user,
            password,
            path,
            margin_pct=current.backup_size_margin_pct,
        )
        return JSONResponse({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/ayarlar/instance/{instance_id}/schemas/list")
async def settings_schemas_list(request: Request, instance_id: str):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Oturum gerekli"}, status_code=401)
    if not can_manage_settings(request):
        return JSONResponse({"ok": False, "error": "Ayar yetkisi gerekli"}, status_code=403)

    store = request.app.state.store
    current = store.get()
    target = current.get_instance(instance_id)
    if not target:
        return JSONResponse({"ok": False, "error": "Instance bulunamadi"}, status_code=404)

    sid = str(target.oracle_sid or "").strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "Oracle SID tanimli degil"}, status_code=400)

    result = list_instance_schemas(sid)
    if not result.ok:
        return JSONResponse(
            {"ok": False, "error": result.error, "schemas": [], "oracle_sid": sid},
            status_code=400,
        )
    return JSONResponse(
        {"ok": True, "schemas": result.schemas, "oracle_sid": result.oracle_sid},
    )


@router.post("/ayarlar/instance/{instance_id}/ftp/delete")
async def settings_ftp_delete(request: Request, instance_id: str):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Oturum gerekli"}, status_code=401)
    if not can_manage_settings(request):
        return JSONResponse({"ok": False, "error": "Ayar yetkisi gerekli"}, status_code=403)

    store = request.app.state.store
    current = store.get()
    target = current.get_instance(instance_id)
    if not target:
        return JSONResponse({"ok": False, "error": "Instance bulunamadi"}, status_code=404)

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}

    host = str(body.get("host", "")).strip() or target.localftpip
    user = str(body.get("user", "")).strip() or target.localftpuser
    password = str(body.get("password", "")) or target.localftppass
    path = str(body.get("path", "")).strip() or (target.localftpdir or "/")
    files = body.get("files") or []

    if not isinstance(files, list):
        return JSONResponse({"ok": False, "error": "files listesi gerekli"}, status_code=400)
    if not host:
        return JSONResponse({"ok": False, "error": "FTP sunucu adresi bos"}, status_code=400)
    if not user:
        return JSONResponse({"ok": False, "error": "FTP kullanici adi bos"}, status_code=400)

    try:
        result = delete_files(
            host,
            user,
            password,
            path,
            [str(name) for name in files],
            margin_pct=current.backup_size_margin_pct,
        )
        return JSONResponse({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/ayarlar/instance/{instance_id}/kaydet")
async def settings_save_instance(request: Request, instance_id: str):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    store = request.app.state.store
    current = store.get()
    target = current.get_instance(instance_id)
    if not target:
        return RedirectResponse(url="/ayarlar?error=Instance+bulunamadi", status_code=303)

    form = await request.form()
    try:
        updated_row = _parse_instance_form(form, target, current.yedek_dir)
        validation_errors = _validate_instance(updated_row)
        if validation_errors:
            error_text = quote_plus("Kayit yapilamadi: " + " · ".join(validation_errors))
            return RedirectResponse(
                url=f"/ayarlar?instance={instance_id}&error={error_text}",
                status_code=303,
            )
        payload = _replace_instance_in_settings(current, instance_id, updated_row)
        merged, probes, probe_errors = _merge_oracle_probes(payload)
        store.replace(merged)
        label = target.display_name()
        message = f"{label} kaydedildi ve uygulandi (v{store.version})"
        if probe_errors:
            message += f" · Uyari: {'; '.join(probe_errors)}"
        return RedirectResponse(
            url=f"/ayarlar?instance={instance_id}&message={quote_plus(message)}",
            status_code=303,
        )
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            url=f"/ayarlar?instance={instance_id}&error={quote_plus(f'Kayit hatasi: {exc}')}",
            status_code=303,
        )


@router.post("/ayarlar/genel/kaydet")
async def settings_save_global(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    store = request.app.state.store
    current = store.get()
    form = await request.form()

    try:
        payload = _parse_global_form(form, current)
        merged, probes, probe_errors = _merge_oracle_probes(payload)
        store.replace(merged)
        message = f"Genel ayarlar kaydedildi (v{store.version})"
        if probe_errors:
            message += f" · Uyari: {'; '.join(probe_errors)}"
        return RedirectResponse(
            url=f"/ayarlar?instance=sistem&message={quote_plus(message)}",
            status_code=303,
        )
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(
            url=f"/ayarlar?instance=sistem&error={quote_plus(f'Kayit hatasi: {exc}')}",
            status_code=303,
        )


@router.post("/ayarlar")
async def settings_save(request: Request):
    """Geriye uyumluluk: tum formu tek seferde kaydetmek yerine yonlendir."""
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()
    return RedirectResponse(
        url="/ayarlar?error=Ayarlar+sekme+sekme+kaydedilir.+Her+sekmedeki+Kaydet+dugmesini+kullanin",
        status_code=303,
    )


@router.post("/ayarlar/instance/ekle")
async def settings_add_instance(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    form = await request.form()
    hastane = str(form.get("new_hastane", "")).strip()
    oracle_sid = str(form.get("new_oracle_sid", "")).strip()
    il = normalize_upper_ascii(str(form.get("new_il", "")))
    if not hastane or not oracle_sid:
        return RedirectResponse(url="/ayarlar?error=Hastane+ve+Oracle+SID+zorunlu", status_code=303)

    store = request.app.state.store
    current = store.get()
    base_id = slugify(str(form.get("new_id", "")).strip() or hastane or oracle_sid)
    existing = {inst.id for inst in current.instances}
    instance_id = base_id
    suffix = 2
    while instance_id in existing:
        instance_id = f"{base_id}-{suffix}"
        suffix += 1

    probe = probe_instance(oracle_sid, "")
    yedek_dir = current.yedek_dir
    directorydizini = f"{yedek_dir.rstrip('/')}/"
    if probe.ok:
        yedek_dir = probe.yedek_dir or yedek_dir
        directorydizini = probe.directorydizini or directorydizini

    new_instance = InstanceSettings(
        id=instance_id,
        enabled=True,
        label=hastane,
        hastane=hastane,
        il=il,
        oracle_sid=oracle_sid,
        schemas="SYSTEM",
        directory=ORACLE_DIRECTORY_NAME,
        directorydizini=directorydizini,
        yedek_kodu="Hbys",
        guid_key=str(uuid.uuid4()),
        password="",
    )
    payload = current.model_dump()
    payload["instances"] = [*payload["instances"], new_instance.model_dump()]
    if probe.ok:
        payload["yedek_dir"] = yedek_dir
        if probe.oracle_ver:
            payload["oracle_ver"] = probe.oracle_ver
        if probe.hostname:
            payload["hostname"] = probe.hostname
    store.replace(payload)
    if not probe.ok:
        return RedirectResponse(
            url=f"/ayarlar?message=Instance+eklendi:+{instance_id}&error=Oracle+dogrulama:+{probe.error}",
            status_code=303,
        )
    return RedirectResponse(url=f"/ayarlar?message=Instance+eklendi+ve+Oracle+dogrulandi:+{instance_id}", status_code=303)


@router.post("/ayarlar/instance/{instance_id}/sil")
async def settings_delete_instance(request: Request, instance_id: str):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    store = request.app.state.store
    current = store.get()
    remaining = [inst for inst in current.instances if inst.id != instance_id]
    if len(remaining) == len(current.instances):
        return RedirectResponse(url="/ayarlar?error=Instance+bulunamadi", status_code=303)
    if not remaining:
        return RedirectResponse(url="/ayarlar?error=Son+instance+silinemez", status_code=303)

    payload = current.model_dump()
    payload["instances"] = [inst.model_dump() for inst in remaining]
    store.replace(payload)
    return RedirectResponse(url="/ayarlar?message=Instance+silindi", status_code=303)


@router.post("/ayarlar/instance/{instance_id}/zamanlama/ekle")
async def schedule_add(request: Request, instance_id: str):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    store = request.app.state.store
    current = store.get()
    form = await request.form()
    try:
        rule = _parse_schedule_form(form)
        existing_ids = {
            str(item.get("id", ""))
            for inst in current.instances
            if inst.id == instance_id
            for item in inst.model_dump().get("schedules", [])
        }
        if rule.id in existing_ids:
            rule = rule.model_copy(
                update={"id": _unique_schedule_id(existing_ids, rule.backup_type, rule.time, rule.day_of_week)}
            )

        def add_rule(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [*rows, rule.model_dump()]

        updated = _update_instance_schedules(current, instance_id, add_rule)
        store.replace(updated.model_dump())
        return RedirectResponse(
            url=f"/ayarlar?message=Zamanlama+eklendi:+{instance_id}/{rule.id}",
            status_code=303,
        )
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(url=f"/ayarlar?error=Zamanlama+eklenemedi:+{exc}", status_code=303)


@router.post("/ayarlar/instance/{instance_id}/zamanlama/{rule_id}/duzenle")
async def schedule_edit(request: Request, instance_id: str, rule_id: str):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    store = request.app.state.store
    current = store.get()
    form = await request.form()
    try:
        rule = _parse_schedule_form(form)
        rule = rule.model_copy(update={"id": rule_id})

        def edit_rule(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            updated_rows: list[dict[str, Any]] = []
            found = False
            for row in rows:
                if str(row.get("id")) == rule_id:
                    updated_rows.append(rule.model_dump())
                    found = True
                else:
                    updated_rows.append(row)
            if not found:
                raise ValueError("Zamanlama bulunamadi")
            return updated_rows

        updated = _update_instance_schedules(current, instance_id, edit_rule)
        store.replace(updated.model_dump())
        return RedirectResponse(
            url=f"/ayarlar?message=Zamanlama+guncellendi:+{instance_id}/{rule_id}",
            status_code=303,
        )
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(url=f"/ayarlar?error=Zamanlama+guncellenemedi:+{exc}", status_code=303)


@router.post("/ayarlar/instance/{instance_id}/zamanlama/{rule_id}/sil")
async def schedule_delete(request: Request, instance_id: str, rule_id: str):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    store = request.app.state.store
    current = store.get()
    try:
        def drop_rule(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            remaining = [row for row in rows if str(row.get("id")) != rule_id]
            if len(remaining) == len(rows):
                raise ValueError("Zamanlama bulunamadi")
            return remaining

        updated = _update_instance_schedules(current, instance_id, drop_rule)
        store.replace(updated.model_dump())
        return RedirectResponse(url="/ayarlar?message=Zamanlama+silindi", status_code=303)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(url=f"/ayarlar?error=Zamanlama+silinemedi:+{exc}", status_code=303)
