import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config.models import InstanceSettings, YedekSettings
from app.services.backups import instance_dir, list_backups

logger = logging.getLogger(__name__)

DEFAULT_ESTIMATE_GUNLUK_GB = 3.0
DEFAULT_ESTIMATE_HAFTALIK_GB = 10.0
DEFAULT_ESTIMATE_RMAN_FULL_GB = 25.0
DEFAULT_ESTIMATE_RMAN_INCR_GB = 5.0
WEEKLY_SIZE_FACTOR = 1.35


@dataclass(frozen=True)
class DiskSpaceCheck:
    ok: bool
    reason: str
    free_gb: float
    used_pct: int
    required_gb: float
    estimate_source: str


def disk_thresholds(settings: YedekSettings) -> dict[str, float | int]:
    return {
        "max_usage_pct": settings.backup_disk_max_pct,
        "min_free_gb": settings.backup_disk_min_free_gb,
        "reserve_gb": settings.backup_disk_reserve_gb,
        "margin_pct": settings.backup_size_margin_pct,
        "estimate_gunluk_gb": DEFAULT_ESTIMATE_GUNLUK_GB,
        "estimate_haftalik_gb": DEFAULT_ESTIMATE_HAFTALIK_GB,
        "estimate_rman_full_gb": DEFAULT_ESTIMATE_RMAN_FULL_GB,
        "estimate_rman_incr_gb": DEFAULT_ESTIMATE_RMAN_INCR_GB,
        "yedek_dir": settings.yedek_dir,
    }


def estimate_required_gb(
    settings: YedekSettings,
    instance: InstanceSettings,
    backup_type: str,
) -> tuple[float, str]:
    thresholds = disk_thresholds(settings)
    margin = 1.0 + (int(thresholds["margin_pct"]) / 100.0)
    items = list_backups(settings, instance, limit=5)

    if backup_type == "HAFTALIK":
        weekly = [item for item in items if item.archive_name]
        # Haftalik yedek genelde daha buyuk; varsa en buyuk son kaydi baz al
        if weekly:
            largest_mb = max(item.size_mb for item in weekly)
            return (largest_mb / 1024.0) * margin * WEEKLY_SIZE_FACTOR, "last_backup"
        return float(thresholds["estimate_haftalik_gb"]), "default_haftalik"

    if backup_type in {"RMAN_FULL", "RMAN_FULL_MANUAL"}:
        return float(thresholds.get("estimate_rman_full_gb", DEFAULT_ESTIMATE_RMAN_FULL_GB)), "default_rman_full"

    if backup_type == "RMAN_INCR":
        return float(thresholds.get("estimate_rman_incr_gb", DEFAULT_ESTIMATE_RMAN_INCR_GB)), "default_rman_incr"

    if items:
        return (items[0].size_mb / 1024.0) * margin, "last_backup"
    return float(thresholds["estimate_gunluk_gb"]), "default_gunluk"


def check_backup_disk_space(
    settings: YedekSettings,
    instance: InstanceSettings,
    backup_type: str,
) -> DiskSpaceCheck:
    thresholds = disk_thresholds(settings)
    root = instance_dir(settings, instance)
    root.mkdir(parents=True, exist_ok=True)

    try:
        usage = shutil.disk_usage(root)
    except OSError as exc:
        return DiskSpaceCheck(
            ok=False,
            reason=f"Disk bilgisi okunamadi: {exc}",
            free_gb=0.0,
            used_pct=100,
            required_gb=0.0,
            estimate_source="error",
        )

    total = usage.total or 1
    used_pct = int(usage.used * 100 / total)
    free_gb = usage.free / (1024**3)
    required_gb, estimate_source = estimate_required_gb(settings, instance, backup_type)
    reserve_gb = float(thresholds["reserve_gb"])
    min_free_gb = float(thresholds["min_free_gb"])
    max_pct = int(thresholds["max_usage_pct"])
    need_gb = required_gb + reserve_gb

    if used_pct >= max_pct:
        return DiskSpaceCheck(
            ok=False,
            reason=(
                f"Yedek disk dolulugu %{used_pct} — limit %{max_pct}. "
                "Zamanlanmis yedek baslatilmadi."
            ),
            free_gb=free_gb,
            used_pct=used_pct,
            required_gb=required_gb,
            estimate_source=estimate_source,
        )

    if free_gb < min_free_gb:
        return DiskSpaceCheck(
            ok=False,
            reason=(
                f"Yedek diskte yalnizca {free_gb:.1f} GB bos alan var — "
                f"minimum {min_free_gb:.1f} GB gerekli."
            ),
            free_gb=free_gb,
            used_pct=used_pct,
            required_gb=required_gb,
            estimate_source=estimate_source,
        )

    if free_gb < need_gb:
        return DiskSpaceCheck(
            ok=False,
            reason=(
                f"Tahmini yedek {required_gb:.1f} GB + {reserve_gb:.1f} GB guvenlik payi icin "
                f"yetersiz alan (bos: {free_gb:.1f} GB)."
            ),
            free_gb=free_gb,
            used_pct=used_pct,
            required_gb=required_gb,
            estimate_source=estimate_source,
        )

    return DiskSpaceCheck(
        ok=True,
        reason="Yeterli disk alani",
        free_gb=free_gb,
        used_pct=used_pct,
        required_gb=required_gb,
        estimate_source=estimate_source,
    )


def record_backup_skip(
    yedek_dir: Path,
    instance_id: str,
    backup_type: str,
    check: DiskSpaceCheck,
    *,
    scheduled: bool = False,
) -> None:
    yedek_dir.mkdir(parents=True, exist_ok=True)
    status_file = yedek_dir / ".backup-status.json"
    payload = {
        "state": "skipped",
        "tip": backup_type,
        "instance_id": instance_id,
        "exit_code": 0,
        "log_file": "",
        "reason": check.reason,
        "free_gb": round(check.free_gb, 2),
        "used_pct": check.used_pct,
        "required_gb": round(check.required_gb, 2),
        "estimate_source": check.estimate_source,
        "scheduled": scheduled,
        "updated_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    status_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    log_file = yedek_dir / "backup-skip.log"
    line = (
        f"{payload['updated_at']} SKIP scheduled={scheduled} "
        f"instance={instance_id} tip={backup_type} "
        f"free={check.free_gb:.1f}GB used={check.used_pct}% "
        f"need={check.required_gb:.1f}GB reason={check.reason}\n"
    )
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(line)

    logger.warning("Yedek atlandi (disk): %s %s — %s", backup_type, instance_id, check.reason)


def check_rman_disk_space(
    settings: YedekSettings,
    instance: InstanceSettings,
    backup_type: str,
) -> DiskSpaceCheck:
    thresholds = disk_thresholds(settings)
    root = Path(instance.effective_rman_dest())
    root.mkdir(parents=True, exist_ok=True)

    try:
        usage = shutil.disk_usage(root)
    except OSError as exc:
        return DiskSpaceCheck(
            ok=False,
            reason=f"RMAN disk bilgisi okunamadi: {exc}",
            free_gb=0.0,
            used_pct=100,
            required_gb=0.0,
            estimate_source="error",
        )

    total = usage.total or 1
    used_pct = int(usage.used * 100 / total)
    free_gb = usage.free / (1024**3)
    required_gb, estimate_source = estimate_required_gb(settings, instance, backup_type)
    reserve_gb = float(thresholds["reserve_gb"])
    min_free_gb = float(thresholds["min_free_gb"])
    max_pct = int(thresholds["max_usage_pct"])
    need_gb = required_gb + reserve_gb

    if used_pct >= max_pct:
        return DiskSpaceCheck(
            ok=False,
            reason=(
                f"RMAN disk dolulugu %{used_pct} — limit %{max_pct}. "
                "Zamanlanmis yedek baslatilmadi."
            ),
            free_gb=free_gb,
            used_pct=used_pct,
            required_gb=required_gb,
            estimate_source=estimate_source,
        )

    if free_gb < min_free_gb:
        return DiskSpaceCheck(
            ok=False,
            reason=(
                f"RMAN diskte yalnizca {free_gb:.1f} GB bos alan var — "
                f"minimum {min_free_gb:.1f} GB gerekli."
            ),
            free_gb=free_gb,
            used_pct=used_pct,
            required_gb=required_gb,
            estimate_source=estimate_source,
        )

    if free_gb < need_gb:
        return DiskSpaceCheck(
            ok=False,
            reason=(
                f"RMAN tahmini yedek {required_gb:.1f} GB + {reserve_gb:.1f} GB guvenlik payi icin "
                f"yetersiz alan (bos: {free_gb:.1f} GB)."
            ),
            free_gb=free_gb,
            used_pct=used_pct,
            required_gb=required_gb,
            estimate_source=estimate_source,
        )

    return DiskSpaceCheck(
        ok=True,
        reason="Yeterli disk alani",
        free_gb=free_gb,
        used_pct=used_pct,
        required_gb=required_gb,
        estimate_source=estimate_source,
    )
