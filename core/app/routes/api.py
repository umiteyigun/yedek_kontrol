import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Query, Request

from app.auth import get_current_user
from app.services.server_time import collect_host_clock

from app.services.disk_report import collect_disk_areas
from app.services.notifications import NotificationService, build_payload

from app.services.agent_snapshot import collect_agent_snapshot

router = APIRouter(tags=["api"])

LOCAL_AGENT_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _disk_pct(path: str) -> str:
    try:
        usage = shutil.disk_usage(path)
        return f"{int(usage.used * 100 / usage.total)}%"
    except OSError:
        return "0%"


@router.get("/api/v1/agent/snapshot")
def agent_snapshot(request: Request):
    """Yerel agent metrik toplama — yalnizca localhost."""
    client_host = (request.client.host if request.client else "").lower()
    if client_host not in LOCAL_AGENT_HOSTS:
        return {"ok": False, "error": "Yalnizca yerel agent"}
    store = request.app.state.store
    yedek_dir = Path(request.app.state.yedek_dir)
    return {"ok": True, **collect_agent_snapshot(store, yedek_dir)}


@router.get("/api/server/clock")
def server_clock(request: Request):
    if not get_current_user(request):
        return {"ok": False, "error": "Oturum gerekli"}
    clock = collect_host_clock()
    return {"ok": clock.get("clock_ok", False), **clock}


@router.get("/health")
def health(request: Request):
    store = request.app.state.store
    settings = store.get()
    yedek_dir = Path(request.app.state.yedek_dir)
    backups = sorted(yedek_dir.glob("*.dmp.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    latest = backups[0] if backups else None
    return {
        "status": "ok",
        "config_version": store.version,
        "applied_at": store.applied_at.isoformat() if store.applied_at else None,
        "hastane": settings.hastane,
        "yedek_dir": str(yedek_dir),
        "last_backup": latest.name if latest else None,
        "ftp_port": settings.ftp_port,
    }


@router.get("/api/v1/config")
def live_config(request: Request):
    store = request.app.state.store
    settings = store.get()
    return {
        "version": store.version,
        "applied_at": store.applied_at.isoformat() if store.applied_at else None,
        "settings": settings.public_dict(),
    }


@router.get("/api/v1/bildirimler")
def list_notifications(request: Request, limit: int = 20):
    service: NotificationService = request.app.state.notifications
    return {"items": service.recent(limit)}


@router.get("/api/YedekYonetimi/YedekBildirimi")
async def yedek_bildirimi(
    request: Request,
    background_tasks: BackgroundTasks,
    GuidKey: str = Query(""),
    YedekKodu: str = Query(""),
    KurumNo: str = Query(""),
    Tarih: str = Query(""),
    DisIp: str = Query(""),
    DiskAlani1: str = Query(""),
    DiskAlani2: str = Query(""),
    DiskAlani3: str = Query("0"),
    YedekBoyutu: str = Query(""),
    Ftp: str = Query(""),
    Mail: str = Query("1"),
    InstanceId: str = Query(""),
    OracleSid: str = Query(""),
    Hastane: str = Query(""),
    Il: str = Query(""),
    Hostname: str = Query(""),
    YedekTipi: str = Query(""),
    DosyaAdi: str = Query(""),
):
    """yedek.sh curl cagrisi + merkezi API iletimi (remote arka planda)."""
    store = request.app.state.store
    settings = store.get()
    yedek_dir = Path(request.app.state.yedek_dir)
    notifications: NotificationService = request.app.state.notifications

    if not YedekBoyutu:
        archive = yedek_dir / DosyaAdi if DosyaAdi else None
        if archive and archive.exists():
            YedekBoyutu = str(archive.stat().st_size)
        else:
            # Glob NFS'te takilmasin — sadece boyut yoksa ve DosyaAdi yoksa
            try:
                backups = sorted(
                    yedek_dir.glob("*.dmp.gz"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                YedekBoyutu = str(backups[0].stat().st_size) if backups else "-1"
            except OSError:
                YedekBoyutu = "-1"

    backup_mount_path = str(yedek_dir)
    if DosyaAdi:
        backup_mount_path = str((yedek_dir / DosyaAdi).parent)
    elif InstanceId:
        inst_match = settings.get_instance(InstanceId)
        if inst_match:
            backup_mount_path = inst_match.effective_directorydizini(settings.yedek_dir)

    # Disk alanlari yedek.sh'den geliyorsa tekrar df yapma (NFS hang riski)
    if DiskAlani1:
        disks = {
            "DiskAlani1": DiskAlani1,
            "DiskAlani2": DiskAlani2 if DiskAlani2 not in ("",) else "0",
            "DiskAlani3": DiskAlani3 if DiskAlani3 not in ("",) else "0",
        }
    else:
        disks = collect_disk_areas(backup_mount_path)

    payload = build_payload(
        settings,
        guid_key=GuidKey,
        yedek_kodu=YedekKodu,
        kurumkodu=KurumNo,
        tarih=Tarih,
        dis_ip=DisIp,
        disk1=DiskAlani1 or disks["DiskAlani1"],
        disk2=DiskAlani2 if DiskAlani2 not in ("", "0") else disks["DiskAlani2"],
        disk3=DiskAlani3 if DiskAlani3 not in ("", "0") else disks["DiskAlani3"],
        yedek_boyutu=YedekBoyutu,
        ftp=Ftp,
        mail=Mail,
        yedek_tipi=YedekTipi,
        dosya_adi=DosyaAdi,
        instance_id=InstanceId,
        oracle_sid=OracleSid,
    )

    if KurumNo:
        payload["KurumNo"] = KurumNo
    if Hastane:
        payload["Hastane"] = Hastane
    if Il:
        payload["Il"] = Il
    if Hostname:
        payload["Hostname"] = Hostname

    payload["config_version"] = store.version
    entry = notifications.record(payload)

    # Yerel kayit hemen bitsin; kurumsalapi yedek kilidini tutmasin
    remote_url = settings.remote_api_url or ""
    if remote_url:
        background_tasks.add_task(notifications.forward_remote, remote_url, dict(payload))
        forward = {"forwarded": True, "queued": True, "url": remote_url}
    else:
        forward = {"forwarded": False, "reason": "remote_api_url bos"}

    return {"ok": True, "data": entry, "remote": forward}
