import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Query

app = FastAPI(title="Yedek API", version="1.0.0")

YEDEK_DIR = Path("/yedek")
HASTANE = os.getenv("HASTANE", "UNKNOWN")
YEDEK_KODU = os.getenv("YEDEK_KODU", "Hbys")
GUID_KEY = os.getenv("GUID_KEY", "")
REMOTE_API_URL = os.getenv("REMOTE_API_URL", "")


def disk_usage_pct(path: str) -> str:
    try:
        usage = shutil.disk_usage(path)
        return f"{int(usage.used * 100 / usage.total)}%"
    except OSError:
        return "0%"


def latest_backup() -> Path | None:
    files = sorted(YEDEK_DIR.glob("*.dmp.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


@app.get("/health")
def health():
    backup = latest_backup()
    return {
        "status": "ok",
        "hastane": HASTANE,
        "yedek_dir": str(YEDEK_DIR),
        "last_backup": backup.name if backup else None,
        "last_backup_mtime": datetime.fromtimestamp(backup.stat().st_mtime, tz=timezone.utc).isoformat()
        if backup
        else None,
    }


@app.get("/api/YedekYonetimi/YedekBildirimi")
async def yedek_bildirimi(
    GuidKey: str = Query(""),
    YedekKodu: str = Query(""),
    Tarih: str = Query(""),
    DisIp: str = Query(""),
    DiskAlani1: str = Query(""),
    DiskAlani2: str = Query(""),
    DiskAlani3: str = Query("0"),
    YedekBoyutu: str = Query(""),
    Ftp: str = Query(""),
    Mail: str = Query("1"),
):
    """Mevcut yedek.sh curl cagrisiyla uyumlu endpoint."""
    backup = latest_backup()
    size = backup.stat().st_size if backup else -1

    payload = {
        "GuidKey": GuidKey or GUID_KEY,
        "YedekKodu": YedekKodu or YEDEK_KODU,
        "Tarih": Tarih or datetime.now().strftime("%Y%m%d"),
        "DisIp": DisIp,
        "DiskAlani1": DiskAlani1 or disk_usage_pct("/"),
        "DiskAlani2": DiskAlani2 or disk_usage_pct(str(YEDEK_DIR)),
        "DiskAlani3": DiskAlani3,
        "YedekBoyutu": YedekBoyutu or str(size),
        "Ftp": Ftp,
        "Mail": Mail,
        "Hastane": HASTANE,
        "received_at": datetime.now(timezone.utc).isoformat(),
    }

    if REMOTE_API_URL:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                await client.get(REMOTE_API_URL, params=payload)
        except httpx.HTTPError:
            pass

    return {"ok": True, "data": payload}
