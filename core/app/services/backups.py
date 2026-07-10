import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config.models import InstanceSettings, YedekSettings

STAGE_ORDER = ("preflight", "exporting", "compressing", "ftp_upload", "notifying")
STALE_RUNNING_WITHOUT_LOCK_SEC = 600
STAGE_LABELS = {
    "preflight": "On kontrol",
    "exporting": "Yedek aliniyor",
    "compressing": "Sikistiriliyor",
    "ftp_upload": "FTP gonderimi",
    "notifying": "Bildirim",
    "done": "Tamamlandi",
}


def _parse_iso_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def format_duration(sec: int | float | None) -> str:
    if sec is None or sec < 0:
        return ""
    total = int(sec)
    if total < 60:
        return f"{total} sn"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} dk" + (f" {seconds} sn" if seconds else "")
    hours, minutes = divmod(minutes, 60)
    return f"{hours} sa {minutes} dk"


def _live_duration_sec(started_at: str | None) -> int | None:
    start = _parse_iso_ts(started_at)
    if not start:
        return None
    now = datetime.now(timezone.utc)
    return max(0, int((now - start.astimezone(timezone.utc)).total_seconds()))


def enrich_backup_status(data: dict) -> dict:
    """Asama surelerini okunabilir forma getir."""
    out = dict(data)
    stages_raw = out.get("stages") if isinstance(out.get("stages"), dict) else {}
    stages: list[dict] = []
    current_stage = str(out.get("stage") or "")
    state = str(out.get("state") or "idle")

    for key in STAGE_ORDER:
        raw = stages_raw.get(key)
        if not isinstance(raw, dict):
            continue
        ended = raw.get("ended_at")
        duration_sec = raw.get("duration_sec")
        duration_label = str(raw.get("duration_label") or "")
        if duration_sec is None and not ended and key == current_stage and state == "running":
            duration_sec = _live_duration_sec(raw.get("started_at") or out.get("stage_started_at"))
            if duration_sec is not None:
                duration_label = format_duration(duration_sec)
        elif duration_sec is not None and not duration_label:
            duration_label = format_duration(duration_sec)
        stages.append(
            {
                "key": key,
                "label": STAGE_LABELS.get(key, key),
                "started_at": raw.get("started_at") or "",
                "ended_at": ended or "",
                "duration_sec": duration_sec,
                "duration_label": duration_label,
                "active": key == current_stage and state == "running",
                "done": bool(ended) or (state in {"done", "failed", "skipped"} and key != current_stage),
            }
        )

    out["stages_list"] = stages
    out["stage_label"] = STAGE_LABELS.get(current_stage, current_stage or "—")
    if state == "running" and current_stage:
        live = _live_duration_sec(out.get("stage_started_at"))
        if live is not None:
            out["stage_live_duration_sec"] = live
            out["stage_live_duration_label"] = format_duration(live)

    total_sec = out.get("total_duration_sec")
    if total_sec is None and out.get("started_at") and state in {"done", "failed", "skipped"}:
        start = _parse_iso_ts(str(out.get("started_at")))
        end = _parse_iso_ts(str(out.get("updated_at")))
        if start and end:
            total_sec = max(0, int((end - start.astimezone(timezone.utc)).total_seconds()))
    if total_sec is not None and not out.get("total_duration_label"):
        out["total_duration_label"] = format_duration(total_sec)
    return out


@dataclass
class BackupItem:
    base_name: str
    archive_name: str
    log_name: str | None
    size_mb: float
    mtime: str
    has_log: bool
    instance_id: str


def _safe_name(name: str) -> str:
    if not re.fullmatch(r"[\w.\-]+", name):
        raise ValueError("Gecersiz dosya adi")
    return name


def backup_root(settings: YedekSettings) -> Path:
    return Path(settings.yedek_dir)


def instance_dir(settings: YedekSettings, instance: InstanceSettings) -> Path:
    return backup_root(settings)


def resolve_instance(settings: YedekSettings, instance_id: str | None) -> InstanceSettings | None:
    if instance_id:
        return settings.get_instance(instance_id)
    return settings.first_instance()


def _resolve_in_root(root: Path, name: str) -> Path:
    safe = _safe_name(name)
    path = (root / safe).resolve()
    if not str(path).startswith(str(root.resolve())):
        raise ValueError("Erisim reddedildi")
    return path


def _find_archive(root: Path, instance: InstanceSettings, archive_name: str) -> Path | None:
    path = _resolve_in_root(root, archive_name)
    if path.exists() and instance.matches_backup_file(path.name):
        return path
    return None


def _collect_backup_groups(root: Path, instance: InstanceSettings) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    if not root.exists():
        return groups
    for path in root.iterdir():
        if not path.is_file():
            continue
        if not instance.matches_backup_file(path.name):
            continue
        base = instance.backup_base_name(path.name)
        groups.setdefault(base, []).append(path)
    return groups


def list_backups(
    settings: YedekSettings,
    instance: InstanceSettings,
    limit: int = 100,
) -> list[BackupItem]:
    root = backup_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    groups = _collect_backup_groups(root, instance)
    rows: list[tuple[float, BackupItem]] = []

    for base, paths in groups.items():
        paths.sort(key=lambda item: item.name)
        primary = paths[0]
        total_bytes = sum(item.stat().st_size for item in paths)
        latest_mtime = max(item.stat().st_mtime for item in paths)
        log_name = instance.backup_log_name(primary.name)
        log_path = root / log_name
        display_name = primary.name if len(paths) == 1 else f"{primary.name} (+{len(paths)} parca)"
        rows.append(
            (
                latest_mtime,
                BackupItem(
                    base_name=base,
                    archive_name=display_name,
                    log_name=log_name if log_path.exists() else None,
                    size_mb=round(total_bytes / (1024 * 1024), 2),
                    mtime=datetime.fromtimestamp(latest_mtime).strftime("%d.%m.%Y %H:%M"),
                    has_log=log_path.exists(),
                    instance_id=instance.id,
                ),
            )
        )

    rows.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in rows[:limit]]


def list_all_backups(settings: YedekSettings, limit: int = 100) -> list[BackupItem]:
    items: list[BackupItem] = []
    for instance in settings.instances:
        items.extend(list_backups(settings, instance, limit=limit))
    items.sort(key=lambda item: item.mtime, reverse=True)
    return items[:limit]


def _decode_log_bytes(raw: bytes) -> str:
    if not raw:
        return ""
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for encoding in ("cp1254", "iso-8859-9", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_log_file(path: Path) -> str:
    return _decode_log_bytes(path.read_bytes())


def read_log(settings: YedekSettings, instance: InstanceSettings, name: str, tail_lines: int = 0) -> str:
    root = backup_root(settings)
    path = _resolve_in_root(root, name)
    if not path.exists():
        return "Log dosyasi bulunamadi."
    if path.suffix not in {".log"}:
        raise ValueError("Sadece log dosyalari okunabilir")
    if path.name.startswith(("panel-backup-", "panel-rman-", "backup-watcher", "backup-skip")):
        return _read_log_file(path)
    base = instance.backup_base_name(path.name.removesuffix(".log"))
    matched = False
    for archive_name in root.iterdir():
        if archive_name.is_file() and instance.backup_base_name(archive_name.name) == base:
            matched = True
            break
    if not matched:
        raise ValueError("Bu log bu kuruma ait degil")
    lines = _read_log_file(path).splitlines()
    if tail_lines > 0 and len(lines) > tail_lines:
        lines = ["... (son %s satir) ..." % tail_lines, *lines[-tail_lines:]]
    return "\n".join(lines)


def read_panel_log(settings: YedekSettings, name: str) -> str:
    root = backup_root(settings)
    path = _resolve_in_root(root, name)
    if not path.exists():
        raise ValueError("Log dosyasi bulunamadi")
    if path.suffix != ".log":
        raise ValueError("Sadece log dosyalari okunabilir")
    if not path.name.startswith(("panel-backup-", "panel-rman-", "backup-watcher", "backup-skip")):
        raise ValueError("Gecersiz panel log dosyasi")
    return _read_log_file(path)


def resolve_backup_artifacts(
    settings: YedekSettings,
    instance: InstanceSettings,
    base_name: str,
) -> list[tuple[Path, str]]:
    root = backup_root(settings)
    clean = base_name.strip()
    if not clean or not re.fullmatch(r"[\w.\-]+", clean):
        raise ValueError(f"Gecersiz yedek adi: {base_name}")
    groups = _collect_backup_groups(root, instance)
    paths = groups.get(clean)
    if not paths:
        raise ValueError(f"Yedek bulunamadi: {base_name}")
    paths = sorted(paths, key=lambda item: item.name)
    return [(path, path.name) for path in paths]


def resend_backups_to_ftp(
    settings: YedekSettings,
    instance: InstanceSettings,
    base_names: list[str],
) -> dict[str, object]:
    from app.services.ftp_client import upload_files

    if not instance.ftp_upload_enabled:
        raise ValueError("Uzak FTP yuklemesi bu kurumda kapali")

    ftp_host, ftp_user, ftp_pass = instance.effective_ftp(settings)
    if not str(ftp_host).strip():
        raise ValueError("FTP sunucu adresi tanimli degil")
    if not str(ftp_user).strip():
        raise ValueError("FTP kullanici adi tanimli degil")
    if not str(ftp_pass).strip():
        raise ValueError("FTP sifresi tanimli degil")

    remote_dir = instance.localftpdir or "/"
    uploaded: list[str] = []
    failed: list[dict[str, str]] = []

    for base_name in base_names:
        try:
            artifacts = resolve_backup_artifacts(settings, instance, base_name)
        except ValueError as exc:
            failed.append({"name": base_name, "error": str(exc)})
            continue
        try:
            result = upload_files(
                ftp_host,
                ftp_user,
                ftp_pass,
                remote_dir,
                artifacts,
            )
            uploaded.extend(str(name) for name in result.get("uploaded", []))
        except Exception as exc:  # noqa: BLE001
            failed.append({"name": base_name, "error": str(exc)})

    return {
        "uploaded": uploaded,
        "uploaded_count": len(uploaded),
        "failed": failed,
        "failed_count": len(failed),
    }


def delete_backup(settings: YedekSettings, instance: InstanceSettings, archive_name: str) -> list[str]:
    if not (
        archive_name.endswith(".dmp.gz")
        or archive_name.endswith(".dmp")
        or archive_name.endswith(".zip")
        or ".part_" in archive_name
        or "-part-" in archive_name
        or "(+" in archive_name
    ):
        raise ValueError("Gecersiz yedek dosya adi")
    root = backup_root(settings)
    clean_name = archive_name.split(" (+", 1)[0].strip()
    archive = _find_archive(root, instance, clean_name)
    if archive is None:
        raise ValueError("Yedek dosyasi bulunamadi")
    removed: list[str] = []
    base = instance.backup_base_name(archive.name)
    for path in list(root.iterdir()):
        if not path.is_file():
            continue
        if path.name == instance.backup_log_name(archive.name) or (
            instance.matches_backup_file(path.name) and instance.backup_base_name(path.name) == base
        ):
            path.unlink(missing_ok=True)
            removed.append(path.name)
    return removed


def queue_backup(
    trigger_path: Path,
    tip: str,
    instance_id: str = "",
    ftp_target: str = "primary",
) -> None:
    if tip not in {"GUNLUK", "HAFTALIK"}:
        raise ValueError("Tip GUNLUK veya HAFTALIK olmali")
    if instance_id and not re.fullmatch(r"[a-z0-9\-]+", instance_id):
        raise ValueError("Gecersiz instance id")
    if ftp_target not in {"primary", "secondary", "none"}:
        raise ValueError("Gecersiz FTP hedefi")
    if instance_id:
        payload = f"{tip}:{instance_id}:{ftp_target}"
    else:
        payload = tip
    trigger_path.parent.mkdir(parents=True, exist_ok=True)
    trigger_path.write_text(payload, encoding="utf-8")


def queue_rman_backup(trigger_path: Path, tip: str, instance_id: str = "") -> None:
    if tip not in {"RMAN_FULL", "RMAN_INCR", "RMAN_FULL_MANUAL"}:
        raise ValueError("Tip RMAN_FULL, RMAN_INCR veya RMAN_FULL_MANUAL olmali")
    if not instance_id:
        raise ValueError("RMAN icin instance_id gerekli")
    if not re.fullmatch(r"[a-z0-9\-]+", instance_id):
        raise ValueError("Gecersiz instance id")
    payload = f"{tip}:{instance_id}"
    trigger_path.parent.mkdir(parents=True, exist_ok=True)
    trigger_path.write_text(payload, encoding="utf-8")


def backup_status(yedek_dir: Path) -> dict:
    status_file = yedek_dir / ".backup-status.json"
    running_lock = yedek_dir / ".backup-running"
    defaults = {
        "state": "idle",
        "stage": "",
        "tip": "",
        "instance_id": "",
        "exit_code": 0,
        "log_file": "",
        "updated_at": "",
        "reason": "",
        "stages": {},
        "stages_list": [],
        "stage_label": "",
    }
    data = dict(defaults)
    if status_file.exists():
        try:
            loaded = json.loads(status_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
        except json.JSONDecodeError:
            data["state"] = "unknown"
            data["exit_code"] = -1
    if running_lock.exists() and data.get("state") not in {"running"}:
        data["state"] = "running"

    if data.get("state") == "running" and not running_lock.exists():
        updated = _parse_iso_ts(str(data.get("updated_at") or data.get("stage_started_at") or ""))
        if updated:
            age = (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
            if age > STALE_RUNNING_WITHOUT_LOCK_SEC:
                data["state"] = "failed"
                data["exit_code"] = int(data.get("exit_code") or -1)
                if not data.get("reason"):
                    data["reason"] = "Yedek islemi yarida kaldi (kilitsiz / zaman asimi)"
        elif not data.get("stages"):
            data["state"] = "idle"

    data.setdefault("instance_id", "")
    data.setdefault("reason", "")
    data.setdefault("stages", {})
    return enrich_backup_status(data)
