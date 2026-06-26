import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from auth import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    create_session_token,
    get_current_user,
    login_redirect,
    verify_login,
)
from config_manager import apply_settings, import_from_yedekconfig, load_settings, mask_secret, save_settings

app = FastAPI(title="Yedek Yonetim Paneli", version="1.0.0")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

YEDEK_DIR = Path(os.getenv("YEDEK_DIR", "/yedek"))
HOST_YEDEKCONFIG = Path(os.getenv("HOST_YEDEKCONFIG", "/host-config/yedekconfig.sh"))


def _dashboard_context(request: Request, settings: dict[str, Any], message: str = "", error: str = "") -> dict[str, Any]:
    backups = sorted(YEDEK_DIR.glob("*.dmp.gz"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    backup_rows = [
        {
            "name": p.name,
            "size_mb": round(p.stat().st_size / (1024 * 1024), 2),
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%d.%m.%Y %H:%M"),
        }
        for p in backups
    ]

    disk_root = disk_yedek = "?"
    try:
        root = shutil.disk_usage("/")
        disk_root = f"{int(root.used * 100 / root.total)}%"
    except OSError:
        pass
    try:
        yedek = shutil.disk_usage(str(YEDEK_DIR))
        disk_yedek = f"{int(yedek.used * 100 / yedek.total)}%"
    except OSError:
        pass

    return {
        "request": request,
        "user": get_current_user(request),
        "settings": settings,
        "masked_password": mask_secret(settings.get("password", "")),
        "masked_ftp_pass": mask_secret(settings.get("localftppass", "")),
        "backups": backup_rows,
        "disk_root": disk_root,
        "disk_yedek": disk_yedek,
        "message": message,
        "error": error,
    }


@app.on_event("startup")
def startup_import() -> None:
    if HOST_YEDEKCONFIG.exists() and not Path(os.getenv("CONFIG_DIR", "/app/config")).joinpath("settings.json").exists():
        import_from_yedekconfig(HOST_YEDEKCONFIG)
    settings = load_settings()
    apply_settings(settings)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if not verify_login(username, password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Kullanici adi veya sifre hatali."},
            status_code=401,
        )
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(username),
        httponly=True,
        max_age=SESSION_MAX_AGE,
        samesite="lax",
    )
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not get_current_user(request):
        return login_redirect()
    return templates.TemplateResponse("dashboard.html", _dashboard_context(request, load_settings()))


@app.get("/ayarlar", response_class=HTMLResponse)
def settings_page(request: Request):
    if not get_current_user(request):
        return login_redirect()
    return templates.TemplateResponse("settings.html", _dashboard_context(request, load_settings()))


@app.post("/ayarlar")
async def settings_save(request: Request):
    if not get_current_user(request):
        return login_redirect()

    form = await request.form()
    current = load_settings()

    def field(name: str, default: str = "") -> str:
        value = form.get(name)
        return str(value).strip() if value is not None else default

    password = field("password")
    ftp_pass = field("localftppass")

    updated: dict[str, Any] = {
        "hastane": field("hastane", current["hastane"]),
        "il": field("il", current["il"]),
        "password": password if password else current["password"],
        "schemas": field("schemas", current["schemas"]),
        "hostname": field("hostname", current["hostname"]),
        "kurumkodu": field("kurumkodu", current["kurumkodu"]),
        "directory": field("directory", current["directory"]),
        "directorydizini": field("directorydizini", current["directorydizini"]),
        "oracle_ver": field("oracle_ver", current["oracle_ver"]),
        "oracle_sid": field("oracle_sid", current["oracle_sid"]),
        "localftpip": field("localftpip", current["localftpip"]),
        "localftpuser": field("localftpuser", current["localftpuser"]),
        "localftppass": ftp_pass if ftp_pass else current["localftppass"],
        "yedek_kodu": field("yedek_kodu", current["yedek_kodu"]),
        "guid_key": field("guid_key", current["guid_key"]),
        "retention_days": int(field("retention_days", str(current["retention_days"])) or current["retention_days"]),
        "remote_api_url": field("remote_api_url", current["remote_api_url"]),
        "yedek_dir": field("yedek_dir", current["yedek_dir"]),
        "pasv_address": field("pasv_address", current["pasv_address"]),
        "api_port": int(field("api_port", str(current["api_port"])) or current["api_port"]),
        "panel_port": int(field("panel_port", str(current["panel_port"])) or current["panel_port"]),
    }

    try:
        saved = save_settings(updated)
        written = apply_settings(saved)
        message = "Ayarlar kaydedildi ve dosyalar guncellendi: " + ", ".join(written)
        return templates.TemplateResponse(
            "settings.html",
            _dashboard_context(request, saved, message=message),
        )
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            "settings.html",
            _dashboard_context(request, current, error=f"Kayit hatasi: {exc}"),
            status_code=500,
        )
