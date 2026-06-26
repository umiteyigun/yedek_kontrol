import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from app.web.templates_env import templates

from app.auth import can_manage_settings, get_current_user, login_redirect, settings_denied_redirect
from app.config.models import RmanScheduleRule, WEEKDAY_LABELS, YedekSettings, slugify
from app.routes.context import page_context
from app.services import backups as backup_service
from app.services.disk_guard import check_rman_disk_space, record_backup_skip
from app.services.oracle_rman_probe import probe_rman_instance, rman_runtime_map
from app.services import rman_backups as rman_service

router = APIRouter(tags=["rman"])
TRIGGER_PATH = Path("/host-output/backup.trigger")


def _rman_context(request: Request, settings: YedekSettings, message: str = "", error: str = "") -> dict[str, Any]:
    ctx = page_context(request, settings, message=message, error=error)
    active = settings.get_instance(request.query_params.get("instance")) or settings.first_instance()
    runtime = rman_runtime_map(settings.model_dump())
    instances_rman: list[dict[str, Any]] = []
    for inst in settings.instances:
        view = dict(inst.model_dump())
        view["display_name"] = inst.display_name()
        view["effective_rman_dest"] = inst.effective_rman_dest()
        view.update(runtime.get(inst.id, {}))
        view["rman_disk"] = rman_service.rman_disk_usage(inst)
        items = rman_service.list_rman_backups(settings, inst, limit=500) if inst.rman_enabled else []
        view["rman_backup_count"] = len(items)
        view["rman_total_mb"] = round(sum(i.size_mb for i in items), 1)
        view["last_rman"] = items[0] if items else None
        instances_rman.append(view)

    backup_rows: list[dict[str, Any]] = []
    all_count = 0
    total_mb = 0.0
    if active:
        items = rman_service.list_rman_backups(settings, active, limit=100)
        all_items = rman_service.list_rman_backups(settings, active, limit=500)
        all_count = len(all_items)
        total_mb = round(sum(i.size_mb for i in all_items), 1)
        backup_rows = [
            {
                "run_id": item.run_id,
                "backup_type": item.backup_type,
                "backup_type_label": item.backup_type_label,
                "folder_type": item.folder_type,
                "size_mb": item.size_mb,
                "mtime": item.mtime,
                "piece_count": item.piece_count,
                "log_name": item.log_name,
                "instance_id": item.instance_id,
            }
            for item in items
        ]

    active_runtime = runtime.get(active.id, {}) if active else {}
    status = backup_service.backup_status(Path(request.app.state.yedek_dir))

    active_schedules = []
    if active:
        for rule in active.rman_schedules:
            active_schedules.append(
                {
                    **rule.model_dump(),
                    "backup_type_label": rule.backup_type_label(),
                    "summary": rule.summary(),
                }
            )

    yedek_dir = Path(request.app.state.yedek_dir)
    cold_flag = yedek_dir / f".rman-cold-{active.id}.flag" if active else None
    cold_active = cold_flag.is_file() if cold_flag else False

    ctx.update(
        {
            "instances_rman": instances_rman,
            "rman_backups": backup_rows,
            "rman_backup_count": all_count,
            "rman_total_mb": total_mb,
            "rman_status": status,
            "weekday_labels": WEEKDAY_LABELS,
            "active_rman": {
                **(active.model_dump() if active else {}),
                "display_name": active.display_name() if active else "",
                "effective_rman_dest": active.effective_rman_dest() if active else "",
                **active_runtime,
                "rman_disk": rman_service.rman_disk_usage(active) if active else {},
                "rman_schedules": active_schedules,
            },
            "rman_cold_active": cold_active,
            "rman_needs_cold_backup": not bool(active_runtime.get("archivelog")) if active else False,
        }
    )
    return ctx


@router.get("/rman", response_class=HTMLResponse)
def rman_page(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    store = request.app.state.store
    settings = store.get()
    if not settings.instances:
        return templates.TemplateResponse(
            "rman.html",
            _rman_context(request, settings, error="Henuz instance tanimli degil."),
        )
    return templates.TemplateResponse(
        "rman.html",
        _rman_context(
            request,
            settings,
            message=request.query_params.get("message", ""),
            error=request.query_params.get("error", ""),
        ),
    )


@router.post("/rman/kaydet")
async def rman_save_settings(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    store = request.app.state.store
    current = store.get()
    form = await request.form()
    instance_id = str(form.get("instance_id", "")).strip()
    target = current.get_instance(instance_id)
    if not target:
        return RedirectResponse(url="/rman?error=Instance+bulunamadi", status_code=303)

    runtime = probe_rman_instance(target.oracle_sid)
    archivelog_requested = form.get("rman_archivelog_backup") == "1"
    archivelog_backup = archivelog_requested and runtime.archivelog

    try:
        retention = int(str(form.get("rman_retention_days", target.rman_retention_days)))
        channels = int(str(form.get("rman_channels", target.rman_channels)))
    except ValueError:
        return RedirectResponse(url=f"/rman?instance={instance_id}&error=Gecersiz+sayisal+deger", status_code=303)

    updated_inst = target.model_copy(
        update={
            "rman_enabled": form.get("rman_enabled") == "1",
            "rman_dest": str(form.get("rman_dest", target.rman_dest)).strip() or "/yedek/rman",
            "rman_archivelog_backup": archivelog_backup,
            "rman_retention_days": retention,
            "rman_channels": channels,
            "rman_compression": form.get("rman_compression") == "1",
        }
    )

    instances = []
    for inst in current.instances:
        if inst.id == instance_id:
            instances.append(updated_inst)
        else:
            instances.append(inst)

    store.replace(current.model_copy(update={"instances": instances}).model_dump())
    return RedirectResponse(
        url=f"/rman?instance={instance_id}&message=RMAN+ayarlari+kaydedildi",
        status_code=303,
    )


@router.post("/rman/baslat")
async def rman_start_backup(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    form = await request.form()
    tip = str(form.get("tip", "RMAN_FULL")).strip().upper()
    instance_id = str(form.get("instance_id", "")).strip()

    settings = request.app.state.store.get()
    yedek_dir = Path(request.app.state.yedek_dir)
    if (yedek_dir / ".backup-running").exists():
        return RedirectResponse(url="/rman?error=Zaten+calisiyor", status_code=303)

    instance = settings.get_instance(instance_id)
    if not instance:
        return RedirectResponse(url="/rman?error=Instance+bulunamadi", status_code=303)
    if not instance.rman_enabled:
        return RedirectResponse(url=f"/rman?instance={instance_id}&error=RMAN+bu+kurum+icin+kapali", status_code=303)

    runtime = probe_rman_instance(instance.oracle_sid)
    if tip == "RMAN_INCR" and not runtime.archivelog:
        return RedirectResponse(
            url=f"/rman?instance={instance_id}&error=Gunluk+fark+icin+ARCHIVELOG+modu+gerekli",
            status_code=303,
        )

    try:
        disk_check = check_rman_disk_space(settings, instance, tip)
        if not disk_check.ok:
            record_backup_skip(yedek_dir, instance.id, tip, disk_check, scheduled=False)
            return RedirectResponse(
                url=f"/rman?instance={instance.id}&error={quote_plus(disk_check.reason)}",
                status_code=303,
            )
        backup_service.queue_rman_backup(TRIGGER_PATH, tip, instance.id)
        label = {"RMAN_FULL": "Haftalik+Full", "RMAN_INCR": "Gunluk+Fark", "RMAN_FULL_MANUAL": "Manuel+Full"}.get(tip, tip)
        extra = ""
        if not runtime.archivelog and tip in {"RMAN_FULL", "RMAN_FULL_MANUAL"}:
            extra = "+(NOARCHIVELOG:+DB+kisa+sure+kapanacak,+sonra+otomatik+acilacak)"
        return RedirectResponse(
            url=f"/rman?instance={instance.id}&message={label}+RMAN+yedegi+baslatildi{extra}",
            status_code=303,
        )
    except ValueError as exc:
        return RedirectResponse(url=f"/rman?error={exc}", status_code=303)


@router.post("/rman/zamanlama/ekle")
async def rman_schedule_add(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    form = await request.form()
    instance_id = str(form.get("instance_id", "")).strip()
    store = request.app.state.store
    current = store.get()
    target = current.get_instance(instance_id)
    if not target:
        return RedirectResponse(url="/rman?error=Instance+bulunamadi", status_code=303)

    try:
        rule = _parse_rman_schedule_form(form)
    except ValueError as exc:
        return RedirectResponse(url=f"/rman?instance={instance_id}&error={exc}", status_code=303)

    existing_ids = {item.id for item in target.rman_schedules}
    if rule.id in existing_ids:
        rule = rule.model_copy(update={"id": f"{rule.id}-{uuid.uuid4().hex[:6]}"})

    updated = target.model_copy(update={"rman_schedules": [*target.rman_schedules, rule]})
    instances = [updated if inst.id == instance_id else inst for inst in current.instances]
    store.replace(current.model_copy(update={"instances": instances}).model_dump())
    return RedirectResponse(url=f"/rman?instance={instance_id}&message=RMAN+zamanlama+eklendi", status_code=303)


@router.post("/rman/zamanlama/sil")
async def rman_schedule_delete(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    form = await request.form()
    instance_id = str(form.get("instance_id", "")).strip()
    rule_id = str(form.get("rule_id", "")).strip()
    store = request.app.state.store
    current = store.get()
    target = current.get_instance(instance_id)
    if not target:
        return RedirectResponse(url="/rman?error=Instance+bulunamadi", status_code=303)

    updated_rules = [rule for rule in target.rman_schedules if rule.id != rule_id]
    updated = target.model_copy(update={"rman_schedules": updated_rules})
    instances = [updated if inst.id == instance_id else inst for inst in current.instances]
    store.replace(current.model_copy(update={"instances": instances}).model_dump())
    return RedirectResponse(url=f"/rman?instance={instance_id}&message=RMAN+zamanlama+silindi", status_code=303)


def _parse_rman_schedule_form(form) -> RmanScheduleRule:
    backup_type = str(form.get("backup_type", "RMAN_FULL")).strip().upper()
    if backup_type not in {"RMAN_FULL", "RMAN_INCR"}:
        raise ValueError("RMAN tipi gecersiz")
    time_value = str(form.get("time", "03:00")).strip()
    label = str(form.get("label", "")).strip()
    day_raw = form.get("day_of_week")
    day_of_week = int(day_raw) if day_raw not in (None, "") else None
    if backup_type == "RMAN_INCR":
        day_of_week = None
    enabled = form.get("enabled") == "1"
    rule_id = slugify(f"{backup_type}-{time_value}-{day_of_week if day_of_week is not None else 'daily'}")
    return RmanScheduleRule(
        id=rule_id,
        enabled=enabled,
        backup_type=backup_type,  # type: ignore[arg-type]
        time=time_value,
        day_of_week=day_of_week,
        label=label,
    )


@router.get("/rman/log/{run_id}", response_class=HTMLResponse)
def rman_view_log(request: Request, run_id: str):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return RedirectResponse(url="/rman?error=Log+icin+admin+yetkisi+gerekli", status_code=303)

    settings = request.app.state.store.get()
    active = settings.get_instance(request.query_params.get("instance")) or settings.first_instance()
    if not active:
        return RedirectResponse(url="/rman?error=Instance+bulunamadi", status_code=303)

    try:
        content = rman_service.read_rman_log(active, run_id)
    except ValueError as exc:
        return RedirectResponse(url=f"/rman?instance={active.id}&error={exc}", status_code=303)

    ctx = page_context(request, settings)
    ctx.update({"name": run_id, "content": content, "log_back_url": f"/rman?instance={active.id}"})
    return templates.TemplateResponse("yedek_log.html", ctx)


@router.post("/rman/sil")
async def rman_delete_backup(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can_manage_settings(request):
        return settings_denied_redirect()

    form = await request.form()
    run_id = str(form.get("run_id", "")).strip()
    instance_id = str(form.get("instance_id", "")).strip()
    settings = request.app.state.store.get()
    active = settings.get_instance(instance_id) or settings.first_instance()
    if not active:
        return RedirectResponse(url="/rman?error=Instance+bulunamadi", status_code=303)

    try:
        removed = rman_service.delete_rman_backup(active, run_id)
        msg = "Silindi: " + ", ".join(removed)
        return RedirectResponse(url=f"/rman?instance={active.id}&message={msg}", status_code=303)
    except ValueError as exc:
        return RedirectResponse(url=f"/rman?instance={active.id}&error={exc}", status_code=303)


@router.get("/rman/instance/{instance_id}/probe")
async def rman_probe_api(request: Request, instance_id: str):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Oturum gerekli"}, status_code=401)
    if not can_manage_settings(request):
        return JSONResponse({"ok": False, "error": "Admin yetkisi gerekli"}, status_code=403)

    settings = request.app.state.store.get()
    target = settings.get_instance(instance_id)
    if not target:
        return JSONResponse({"ok": False, "error": "Instance bulunamadi"}, status_code=404)

    result = probe_rman_instance(target.oracle_sid)
    return JSONResponse(
        {
            "ok": result.ok,
            "error": result.error,
            "oracle_sid": result.oracle_sid,
            "log_mode": result.log_mode,
            "archivelog": result.archivelog,
        }
    )
