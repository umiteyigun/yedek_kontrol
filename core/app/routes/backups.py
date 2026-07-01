from pathlib import Path

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from app.web.templates_env import templates

from app.auth import can, get_current_user, login_redirect, permission_denied_redirect
from app.routes.context import page_context
from app.services import backups as backup_service
from app.services import rman_backups as rman_service
from app.services.disk_guard import check_backup_disk_space, record_backup_skip

router = APIRouter(tags=["backups"])
TRIGGER_PATH = Path("/host-output/backup.trigger")


@router.get("/api/log/content")
def log_content_api(
    request: Request,
    name: str = Query(..., min_length=1),
    source: str = Query("expdp"),
    instance_id: str = Query(""),
):
    if not get_current_user(request):
        return JSONResponse({"ok": False, "error": "Oturum gerekli"}, status_code=401)
    if not can(request, "backups", "delete"):
        return JSONResponse({"ok": False, "error": "Log goruntuleme icin yetki gerekli"}, status_code=403)

    settings = request.app.state.store.get()
    clean_source = source.strip().lower()
    clean_name = name.strip()

    try:
        if clean_source == "panel":
            content = backup_service.read_panel_log(settings, clean_name)
        elif clean_source == "rman":
            active = settings.get_instance(instance_id) or settings.first_instance()
            if not active:
                raise ValueError("Instance bulunamadi")
            content = rman_service.read_rman_log(active, clean_name)
        else:
            active = settings.get_instance(instance_id) or settings.first_instance()
            if not active:
                raise ValueError("Instance bulunamadi")
            content = backup_service.read_log(settings, active, clean_name)
        return JSONResponse({"ok": True, "name": clean_name, "content": content})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc), "name": clean_name}, status_code=400)


@router.get("/yedekler", response_class=HTMLResponse)
def backups_page(request: Request):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "backups", "view"):
        return permission_denied_redirect("backups")

    store = request.app.state.store
    settings = store.get()
    active = settings.get_instance(request.query_params.get("instance")) or settings.first_instance()
    if not active:
        return templates.TemplateResponse(
            "yedekler.html",
            page_context(request, settings, error="Henuz instance tanimli degil. Ayarlardan ekleyin."),
        )

    items = backup_service.list_backups(settings, active)
    all_items = backup_service.list_backups(settings, active, limit=500)
    status = backup_service.backup_status(Path(request.app.state.yedek_dir))
    ctx = page_context(request, settings)
    ctx.update(
        {
            "backups": items,
            "backup_count": len(all_items),
            "total_backup_mb": round(sum(item.size_mb for item in all_items), 1),
            "status": status,
            "message": request.query_params.get("message", ""),
            "error": request.query_params.get("error", ""),
        }
    )
    return templates.TemplateResponse("yedekler.html", ctx)


@router.post("/yedekler/baslat")
def start_backup(
    request: Request,
    tip: str = Form("GUNLUK"),
    instance_id: str = Form(""),
):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "backups", "add"):
        return permission_denied_redirect("backups")

    settings = request.app.state.store.get()
    yedek_dir = Path(request.app.state.yedek_dir)
    if (yedek_dir / ".backup-running").exists():
        return RedirectResponse(url="/yedekler?error=Zaten+calisiyor", status_code=303)

    instance = settings.get_instance(instance_id) if instance_id else settings.first_instance()
    if not instance:
        return RedirectResponse(url="/yedekler?error=Instance+bulunamadi", status_code=303)

    try:
        disk_check = check_backup_disk_space(settings, instance, tip)
        if not disk_check.ok:
            record_backup_skip(yedek_dir, instance.id, tip, disk_check, scheduled=False)
            return RedirectResponse(
                url=f"/yedekler?instance={instance.id}&error={disk_check.reason}",
                status_code=303,
            )
        backup_service.queue_backup(TRIGGER_PATH, tip, instance.id)
        target = f"/yedekler?instance={instance.id}&message={tip}+yedegi+baslatildi+({instance.display_name()})"
        return RedirectResponse(url=target, status_code=303)
    except ValueError as exc:
        return RedirectResponse(url=f"/yedekler?error={exc}", status_code=303)


@router.get("/yedekler/log/{name}", response_class=HTMLResponse)
def view_log(request: Request, name: str):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "backups", "delete"):
        return RedirectResponse(url="/yedekler?error=Log+goruntuleme+icin+yetki+gerekli", status_code=303)

    settings = request.app.state.store.get()
    active = settings.get_instance(request.query_params.get("instance")) or settings.first_instance()
    if not active:
        return RedirectResponse(url="/yedekler?error=Instance+bulunamadi", status_code=303)

    try:
        content = backup_service.read_log(settings, active, name)
    except ValueError as exc:
        return RedirectResponse(url=f"/yedekler?error={exc}", status_code=303)

    ctx = page_context(request, settings)
    ctx.update({"name": name, "content": content})
    return templates.TemplateResponse("yedek_log.html", ctx)


@router.post("/yedekler/sil")
def delete_backup(
    request: Request,
    archive_name: str = Form(...),
    instance_id: str = Form(""),
):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "backups", "delete"):
        return permission_denied_redirect("backups")

    settings = request.app.state.store.get()
    active = settings.get_instance(instance_id) or settings.first_instance()
    if not active:
        return RedirectResponse(url="/yedekler?error=Instance+bulunamadi", status_code=303)

    try:
        removed = backup_service.delete_backup(settings, active, archive_name)
        msg = "Silindi: " + ", ".join(removed)
        return RedirectResponse(url=f"/yedekler?instance={active.id}&message={msg}", status_code=303)
    except ValueError as exc:
        return RedirectResponse(url=f"/yedekler?error={exc}", status_code=303)


@router.post("/yedekler/ftp-gonder")
def resend_ftp(
    request: Request,
    instance_id: str = Form(""),
    archive_names: list[str] = Form(default=[]),
):
    if not get_current_user(request):
        return login_redirect()
    if not can(request, "backups", "edit"):
        return permission_denied_redirect("backups")

    settings = request.app.state.store.get()
    active = settings.get_instance(instance_id) or settings.first_instance()
    if not active:
        return RedirectResponse(url="/yedekler?error=Instance+bulunamadi", status_code=303)

    selected = [name.strip() for name in archive_names if name.strip()]
    if not selected:
        return RedirectResponse(
            url=f"/yedekler?instance={active.id}&error=FTP+icin+en+az+bir+yedek+secin",
            status_code=303,
        )

    try:
        result = backup_service.resend_backups_to_ftp(settings, active, selected)
        uploaded = int(result.get("uploaded_count", 0))
        failed = int(result.get("failed_count", 0))
        if failed and not uploaded:
            first_error = ""
            failed_rows = result.get("failed", [])
            if failed_rows and isinstance(failed_rows, list):
                first_error = str(failed_rows[0].get("error", ""))
            return RedirectResponse(
                url=f"/yedekler?instance={active.id}&error=FTP+yukleme+basarisiz:+{first_error}",
                status_code=303,
            )
        if failed:
            msg = f"FTP:+{uploaded}+dosya+gonderildi,+{failed}+yedek+basarisiz"
        else:
            msg = f"FTP:+{uploaded}+dosya+basarili+gonderildi"
        return RedirectResponse(url=f"/yedekler?instance={active.id}&message={msg}", status_code=303)
    except ValueError as exc:
        return RedirectResponse(url=f"/yedekler?instance={active.id}&error={exc}", status_code=303)
