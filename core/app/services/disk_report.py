import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

HOST_DISK_REPORT_SCRIPT = "/yedek/config/disk-report.sh"
CONTAINER_DISK_REPORT_SCRIPT = Path(os.getenv("HOST_OUTPUT", "/host-output")) / "disk-report.sh"

# Sistem mount'lari — yedek diski olarak raporlanmaz
IGNORED_MOUNT_PREFIXES = ("/boot",)


def _resolve_path(path: str) -> str:
    candidate = Path(path).expanduser()
    while candidate != candidate.parent and not candidate.exists():
        candidate = candidate.parent
    return str(candidate or Path("/"))


def _df_fields(path: str) -> tuple[str, str, str]:
    try:
        result = subprocess.run(
            ["df", "-P", path],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
        if len(lines) < 2:
            return "", "0%", "/"
        parts = lines[1].split()
        if len(parts) < 6:
            return "", "0%", "/"
        filesystem = parts[0]
        usage = parts[4]
        mount_point = parts[5]
        if not usage.endswith("%"):
            usage = f"{usage}%"
        return filesystem, usage, mount_point
    except (OSError, subprocess.SubprocessError, IndexError):
        return "", "0%", "/"


def _parse_disk_report_output(stdout: str) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in ("DiskAlani1", "DiskAlani2", "DiskAlani3"):
            values[key] = value
    if "DiskAlani1" in values:
        values.setdefault("DiskAlani2", "0")
        values.setdefault("DiskAlani3", "0")
        return values
    return None


def _is_ignored_mount(mount_point: str) -> bool:
    if not mount_point:
        return False
    return any(
        mount_point == prefix or mount_point.startswith(f"{prefix}/")
        for prefix in IGNORED_MOUNT_PREFIXES
    )


def _collect_disk_areas_local(backup_path: str) -> dict[str, str]:
    resolved = _resolve_path(backup_path or "/yedek/orayedek")

    root_src, disk1, root_mp = _df_fields("/")
    backup_src, backup_pct, backup_mp = _df_fields(resolved)

    disk2 = "0"
    if not _is_ignored_mount(backup_mp):
        if backup_mp and root_mp and backup_mp != root_mp:
            disk2 = backup_pct
        elif backup_src and root_src and backup_src != root_src:
            disk2 = backup_pct

    return {
        "DiskAlani1": disk1 or "0%",
        "DiskAlani2": disk2,
        "DiskAlani3": "0",
    }


def collect_disk_areas(backup_path: str = "/yedek/orayedek") -> dict[str, str]:
    """Host mount namespace'te yedek dizininin ayri disk/partition olup olmadigini tespit eder."""
    resolved = _resolve_path(backup_path or "/yedek/orayedek")

    if CONTAINER_DISK_REPORT_SCRIPT.is_file():
        try:
            proc = subprocess.run(
                ["nsenter", "-t", "1", "-m", "--", HOST_DISK_REPORT_SCRIPT, resolved],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            parsed = _parse_disk_report_output(proc.stdout)
            if parsed:
                return parsed
            if proc.stderr:
                logger.warning("disk-report host stderr: %s", proc.stderr.strip())
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("disk-report host calistirilamadi: %s", exc)

    return _collect_disk_areas_local(resolved)
