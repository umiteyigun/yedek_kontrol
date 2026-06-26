import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config.models import InstanceSettings, YedekSettings


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


def queue_backup(trigger_path: Path, tip: str, instance_id: str = "") -> None:
    if tip not in {"GUNLUK", "HAFTALIK"}:
        raise ValueError("Tip GUNLUK veya HAFTALIK olmali")
    if instance_id and not re.fullmatch(r"[a-z0-9\-]+", instance_id):
        raise ValueError("Gecersiz instance id")
    payload = f"{tip}:{instance_id}" if instance_id else tip
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
    if running_lock.exists():
        return {
            "state": "running",
            "tip": "",
            "instance_id": "",
            "exit_code": 0,
            "log_file": "",
            "updated_at": "",
        }
    if not status_file.exists():
        return {
            "state": "idle",
            "tip": "",
            "instance_id": "",
            "exit_code": 0,
            "log_file": "",
            "updated_at": "",
        }
    try:
        data = json.loads(status_file.read_text(encoding="utf-8"))
        data.setdefault("instance_id", "")
        data.setdefault("reason", "")
        return data
    except json.JSONDecodeError:
        return {
            "state": "unknown",
            "tip": "",
            "instance_id": "",
            "exit_code": -1,
            "log_file": "",
            "updated_at": "",
        }
