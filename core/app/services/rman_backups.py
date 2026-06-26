import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.config.models import InstanceSettings, YedekSettings

RMAN_TYPES = {"RMAN_FULL", "RMAN_INCR", "RMAN_FULL_MANUAL"}
RMAN_TYPE_LABELS = {
    "RMAN_FULL": "Haftalik Full",
    "RMAN_INCR": "Gunluk Fark",
    "RMAN_FULL_MANUAL": "Manuel Full",
}
RMAN_FOLDER_TYPES = {
    "full": "RMAN_FULL",
    "fark": "RMAN_INCR",
    "manuel": "RMAN_FULL_MANUAL",
}


@dataclass
class RmanBackupItem:
    run_id: str
    backup_type: str
    backup_type_label: str
    folder_type: str
    path: Path
    size_mb: float
    mtime: str
    piece_count: int
    log_name: str | None
    instance_id: str


def _safe_name(name: str) -> str:
    if not re.fullmatch(r"[\w.\-/]+", name):
        raise ValueError("Gecersiz ad")
    return name


def instance_rman_root(instance: InstanceSettings) -> Path:
    return Path(instance.effective_rman_dest())


def _infer_backup_type(folder_type: str, run_name: str) -> str:
    if folder_type == "manuel" or "MANUELFULL" in run_name.upper():
        return "RMAN_FULL_MANUAL"
    if folder_type == "fark" or "RMANFARK" in run_name.upper():
        return "RMAN_INCR"
    return "RMAN_FULL"


def _folder_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _run_size_mb(path: Path) -> tuple[float, int]:
    total = 0
    count = 0
    if not path.is_dir():
        return 0.0, 0
    for child in path.iterdir():
        if not child.is_file():
            continue
        if child.suffix == ".log" and child.name.endswith(".log"):
            continue
        if child.name in {"run.rman", "ftp.log", ".api-response"}:
            continue
        try:
            total += child.stat().st_size
            count += 1
        except OSError:
            continue
    return round(total / (1024 * 1024), 2), count


def list_rman_backups(
    settings: YedekSettings,
    instance: InstanceSettings,
    limit: int = 100,
) -> list[RmanBackupItem]:
    root = instance_rman_root(instance)
    rows: list[tuple[float, RmanBackupItem]] = []

    scan_paths: list[tuple[str, Path]] = [
        ("full", root / "full"),
        ("fark", root / "fark"),
        ("manuel", root / "full" / "manuel"),
    ]

    for folder_type, base in scan_paths:
        if not base.is_dir():
            continue
        for run_dir in base.iterdir():
            if not run_dir.is_dir():
                continue
            run_name = run_dir.name
            if not any(run_name.upper().startswith(p.upper()) for p in instance.backup_prefixes()):
                if instance.id.lower() not in run_name.lower():
                    continue
            size_mb, piece_count = _run_size_mb(run_dir)
            if piece_count == 0:
                continue
            backup_type = _infer_backup_type(folder_type, run_name)
            log_candidates = list(run_dir.glob("*.log"))
            log_name = log_candidates[0].name if log_candidates else None
            mtime_ts = _folder_mtime(run_dir)
            mtime = datetime.fromtimestamp(mtime_ts).strftime("%d.%m.%Y %H:%M")
            item = RmanBackupItem(
                run_id=run_name,
                backup_type=backup_type,
                backup_type_label=RMAN_TYPE_LABELS.get(backup_type, backup_type),
                folder_type=folder_type,
                path=run_dir,
                size_mb=size_mb,
                mtime=mtime,
                piece_count=piece_count,
                log_name=log_name,
                instance_id=instance.id,
            )
            rows.append((mtime_ts, item))

    rows.sort(key=lambda row: row[0], reverse=True)
    return [item for _, item in rows[:limit]]


def read_rman_log(instance: InstanceSettings, run_id: str) -> str:
    safe_id = _safe_name(run_id)
    root = instance_rman_root(instance)
    for base in (root / "full", root / "fark", root / "full" / "manuel"):
        log_path = base / safe_id / f"{safe_id}.log"
        if log_path.is_file():
            raw = log_path.read_bytes()
            for encoding in ("utf-8", "cp1254", "latin-1"):
                try:
                    return raw.decode(encoding)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="replace")
    raise ValueError("RMAN log bulunamadi")


def delete_rman_backup(instance: InstanceSettings, run_id: str) -> list[str]:
    safe_id = _safe_name(run_id)
    root = instance_rman_root(instance)
    removed: list[str] = []
    for base in (root / "full", root / "fark", root / "full" / "manuel"):
        target = base / safe_id
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(str(target))
    if not removed:
        raise ValueError("RMAN yedek klasoru bulunamadi")
    return removed


def rman_disk_usage(instance: InstanceSettings) -> dict[str, object]:
    root = instance_rman_root(instance)
    try:
        root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(str(root))
        used_pct = int(usage.used * 100 / usage.total) if usage.total else 0
        free_gb = round(usage.free / (1024**3), 1)
        return {"path": str(root), "used_pct": used_pct, "free_gb": free_gb}
    except OSError:
        return {"path": str(root), "used_pct": 0, "free_gb": 0.0}
