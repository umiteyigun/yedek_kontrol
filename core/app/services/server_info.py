import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from app.config.models import InstanceSettings, YedekSettings
from app.services.oracle_probe import instance_runtime_map

logger = logging.getLogger(__name__)

HOST_INFO_SCRIPT = "/yedek/config/host-info.sh"
ORACLE_STATS_SCRIPT = "/yedek/config/oracle-stats.sh"
CACHE_TTL_SECONDS = 45

_cache: dict[str, Any] = {"at": 0.0, "data": {}}
_sid_stats_cache: dict[str, tuple[float, dict[str, Any]]] = {}

CLOSED_STATS_MESSAGE = "Instance kapali oldugu icin Oracle metrikleri hesaplanamiyor"


def _closed_instance_stats(inst: InstanceSettings) -> dict[str, Any]:
    return {
        "oracle_stats_ok": False,
        "oracle_stats_unavailable": True,
        "oracle_stats_reason": "kapali",
        "oracle_stats_message": CLOSED_STATS_MESSAGE,
        "oracle_stats_sid": inst.oracle_sid,
        "data_size_gb": None,
        "data_size_label": "—",
        "sga_mb": None,
        "sga_label": "—",
        "pga_mb": None,
        "pga_label": "—",
        "oracle_version_full": "",
    }


def get_instance_oracle_stats(
    inst: InstanceSettings,
    *,
    running: bool,
    force_refresh: bool = False,
) -> dict[str, Any]:
    if not running:
        return _closed_instance_stats(inst)

    sid_key = inst.oracle_sid.lower()
    now = time.time()
    cached = _sid_stats_cache.get(sid_key)
    if not force_refresh and cached and now - cached[0] < CACHE_TTL_SECONDS:
        return dict(cached[1])

    stats = collect_oracle_stats(inst.oracle_sid)
    if not stats.get("oracle_stats_ok"):
        stats["oracle_stats_unavailable"] = True
        stats["oracle_stats_reason"] = "hata"
        stats["oracle_stats_message"] = str(
            stats.get("oracle_stats_error") or "Oracle metrikleri okunamadi"
        )
    else:
        stats["oracle_stats_unavailable"] = False
        stats["oracle_stats_reason"] = ""
        stats["oracle_stats_message"] = ""

    _sid_stats_cache[sid_key] = (now, stats)
    return dict(stats)


def collect_all_instance_oracle_stats(
    settings: YedekSettings,
    runtime_map: dict[str, dict[str, object]],
    *,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for inst in settings.instances:
        runtime = runtime_map.get(inst.id, {})
        running = bool(runtime.get("oracle_running"))
        result[inst.id] = get_instance_oracle_stats(
            inst,
            running=running,
            force_refresh=force_refresh,
        )
    return result


def attach_instance_oracle_stats(
    row: dict[str, Any],
    stats: dict[str, Any],
) -> dict[str, Any]:
    return {
        **row,
        **stats,
    }


def _run_host_script(
    script_path: str,
    *args: str,
    timeout: int = 20,
    ipc: bool = False,
) -> tuple[int, str, str]:
    flags = ["-m", "-p"]
    if ipc:
        flags.append("-i")
    cmd = ["nsenter", "-t", "1", *flags, "--", script_path, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "host script zaman asimi"
    except FileNotFoundError:
        return 127, "", "nsenter bulunamadi"


def _parse_json_line(stdout: str) -> dict[str, Any]:
    if not stdout:
        return {}
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {}


def _format_gb_from_mb(mb: int | float) -> str:
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{int(mb)} MB"


def collect_host_info() -> dict[str, Any]:
    code, stdout, stderr = _run_host_script(HOST_INFO_SCRIPT)
    data = _parse_json_line(stdout)
    if not data.get("ok"):
        logger.warning("host-info basarisiz (%s): %s", code, stderr or stdout[:200])
        return {
            "host_ok": False,
            "host_error": stderr or "Host bilgisi okunamadi",
        }

    mem_total = int(data.get("mem_total_mb") or 0)
    mem_used = int(data.get("mem_used_mb") or 0)
    return {
        "host_ok": True,
        "hostname": str(data.get("hostname") or ""),
        "os_name": str(data.get("os_name") or ""),
        "kernel": str(data.get("kernel") or ""),
        "arch": str(data.get("arch") or ""),
        "cpu_model": str(data.get("cpu_model") or ""),
        "cpu_cores": int(data.get("cpu_cores") or 0),
        "load_avg": str(data.get("load_avg") or ""),
        "mem_total": _format_gb_from_mb(mem_total),
        "mem_used": _format_gb_from_mb(mem_used),
        "mem_avail": _format_gb_from_mb(int(data.get("mem_avail_mb") or 0)),
        "mem_used_pct": int(data.get("mem_used_pct") or 0),
        "disk_root_total_gb": float(data.get("disk_root_total_gb") or 0),
        "disk_root_used_gb": float(data.get("disk_root_used_gb") or 0),
        "disk_root_pct": int(data.get("disk_root_pct") or 0),
        "disk_yedek_total_gb": float(data.get("disk_yedek_total_gb") or 0),
        "disk_yedek_used_gb": float(data.get("disk_yedek_used_gb") or 0),
        "disk_yedek_pct": int(data.get("disk_yedek_pct") or 0),
        "clock_epoch": int(data.get("clock_epoch") or 0),
        "clock_datetime": str(data.get("clock_datetime") or ""),
        "clock_date": str(data.get("clock_date") or ""),
        "clock_time": str(data.get("clock_time") or ""),
        "timezone": str(data.get("timezone") or ""),
        "utc_offset": str(data.get("utc_offset") or ""),
    }


def collect_oracle_stats(oracle_sid: str) -> dict[str, Any]:
    code, stdout, stderr = _run_host_script(
        ORACLE_STATS_SCRIPT, oracle_sid, timeout=40, ipc=True
    )
    data = _parse_json_line(stdout)
    if not data.get("ok"):
        return {
            "oracle_stats_ok": False,
            "oracle_stats_error": str(data.get("error") or stderr or "Oracle istatistikleri okunamadi"),
            "oracle_stats_sid": oracle_sid,
        }

    data_gb = data.get("data_size_gb")
    sga_mb = data.get("sga_mb")
    pga_mb = data.get("pga_mb")
    return {
        "oracle_stats_ok": True,
        "oracle_stats_sid": oracle_sid,
        "oracle_stats_error": "",
        "data_size_gb": data_gb,
        "data_size_label": f"{data_gb:.1f} GB" if data_gb is not None else "—",
        "sga_mb": sga_mb,
        "sga_label": f"{int(sga_mb)} MB" if sga_mb is not None else "—",
        "pga_mb": pga_mb,
        "pga_label": f"{int(pga_mb)} MB" if pga_mb is not None else "—",
        "oracle_version_full": str(data.get("oracle_version_full") or ""),
    }



def collect_server_info(
    settings: YedekSettings,
    preferred_instance: InstanceSettings | None = None,
    oracle_stats_map: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    host = collect_host_info()
    info: dict[str, Any] = {
        "host_ok": host.get("host_ok", False),
        "host_error": host.get("host_error", ""),
        "hostname": settings.hostname,
        "os_name": "—",
        "kernel": "—",
        "arch": "—",
        "cpu_model": "—",
        "cpu_cores": 0,
        "load_avg": "—",
        "mem_total": "—",
        "mem_used": "—",
        "mem_avail": "—",
        "mem_used_pct": 0,
        "disk_root_total_gb": 0.0,
        "disk_root_used_gb": 0.0,
        "disk_root_pct": 0,
        "disk_yedek_total_gb": 0.0,
        "disk_yedek_used_gb": 0.0,
        "disk_yedek_pct": 0,
        "clock_ok": False,
        "clock_error": "",
        "clock_epoch": 0,
        "clock_datetime": "—",
        "clock_date": "—",
        "clock_time": "—:—:—",
        "timezone": "—",
        "utc_offset": "",
        "oracle_ver": settings.oracle_ver,
        "instance_count": len(settings.instances),
        "oracle_stats_ok": False,
        "oracle_stats_sid": "",
        "oracle_stats_error": "Acik Oracle instance yok",
        "data_size_label": "—",
        "sga_label": "—",
        "pga_label": "—",
        "oracle_version_full": settings.oracle_ver,
    }
    info.update(host)

    if oracle_stats_map is None:
        runtime_map = instance_runtime_map(settings.model_dump())
        oracle_stats_map = collect_all_instance_oracle_stats(settings, runtime_map)

    all_stats = oracle_stats_map
    open_data_gb = 0.0
    open_count = 0
    for inst in settings.instances:
        stats = all_stats.get(inst.id, {})
        if stats.get("oracle_stats_ok") and stats.get("data_size_gb") is not None:
            open_data_gb += float(stats["data_size_gb"])
            open_count += 1

    preferred_stats = (
        all_stats.get(preferred_instance.id, {})
        if preferred_instance
        else {}
    )
    if preferred_stats:
        info.update(preferred_stats)
    elif all_stats:
        for inst in settings.instances:
            stats = all_stats.get(inst.id, {})
            if stats.get("oracle_stats_ok"):
                info.update(stats)
                break

    info["instance_oracle_stats"] = all_stats
    info["open_oracle_data_total_label"] = (
        f"{open_data_gb:.1f} GB" if open_count else "—"
    )
    info["open_oracle_instance_count"] = open_count

    if host.get("hostname") and not info.get("hostname"):
        info["hostname"] = settings.hostname or host["hostname"]
    elif not info.get("hostname"):
        info["hostname"] = settings.hostname

    return info


def get_server_info(
    settings: YedekSettings,
    preferred_instance: InstanceSettings | None = None,
    *,
    oracle_stats_map: dict[str, dict[str, Any]] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    now = time.time()
    cache_key = preferred_instance.id if preferred_instance else ""
    if (
        not force_refresh
        and oracle_stats_map is None
        and _cache.get("key") == cache_key
        and now - float(_cache.get("at") or 0) < CACHE_TTL_SECONDS
        and _cache.get("data")
    ):
        return dict(_cache["data"])

    if force_refresh:
        global _sid_stats_cache
        _sid_stats_cache = {}
        runtime_map = instance_runtime_map(settings.model_dump())
        oracle_stats_map = collect_all_instance_oracle_stats(
            settings, runtime_map, force_refresh=True
        )

    data = collect_server_info(settings, preferred_instance, oracle_stats_map)
    _cache["at"] = now
    _cache["key"] = cache_key
    _cache["data"] = data
    return data


def clear_server_info_cache() -> None:
    global _cache
    _cache = {"at": 0.0, "data": {}, "key": ""}
