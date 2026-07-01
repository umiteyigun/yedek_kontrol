"""Agent icin panel ozet verisi — localhost snapshot API."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.models import YedekSettings
from app.config.store import ConfigStore
from app.services import backups as backup_service
from app.services import rman_backups as rman_service
from app.services.notifications import NotificationService
from app.services.oracle_probe import instance_runtime_map
from app.services.oracle_rman_probe import rman_runtime_map
from app.services.server_info import collect_all_instance_oracle_stats, get_server_info


def _last_reports_by_instance(config_dir: Path) -> dict[str, dict[str, Any]]:
    """InstanceId -> son YedekBildirimi kaydi (kurumsalapi alanlari)."""
    items = NotificationService(config_dir).recent(limit=200)
    by_inst: dict[str, dict[str, Any]] = {}
    for entry in items:
        iid = str(entry.get("InstanceId") or "").strip()
        if not iid:
            continue
        prev = by_inst.get(iid)
        if not prev or str(entry.get("received_at", "")) > str(prev.get("received_at", "")):
            by_inst[iid] = entry
    return by_inst


def collect_agent_snapshot(
    store: ConfigStore,
    yedek_dir: Path,
) -> dict[str, Any]:
    settings = store.get()
    runtime_map = instance_runtime_map(settings.model_dump())
    rman_runtime = rman_runtime_map(settings.model_dump())
    oracle_stats_map = collect_all_instance_oracle_stats(settings, runtime_map)
    last_reports = _last_reports_by_instance(getattr(store, "_config_dir", Path("/app/config")))

    instances: list[dict[str, Any]] = []
    for inst in settings.instances:
        runtime = runtime_map.get(inst.id, {})
        rman_rt = rman_runtime.get(inst.id, {})
        dump_items = backup_service.list_backups(settings, inst, limit=1)
        last_dump = None
        if dump_items:
            item = dump_items[0]
            last_dump = {
                "name": item.archive_name,
                "mtime": item.mtime,
                "size_mb": item.size_mb,
            }
        last_rman = None
        if inst.rman_enabled:
            rman_items = rman_service.list_rman_backups(settings, inst, limit=1)
            if rman_items:
                ritem = rman_items[0]
                last_rman = {
                    "run_id": ritem.run_id,
                    "mtime": ritem.mtime,
                    "size_mb": ritem.size_mb,
                    "backup_type": ritem.backup_type,
                    "backup_type_label": ritem.backup_type_label,
                }
        instances.append(
            {
                "id": inst.id,
                "label": inst.label or inst.id,
                "hastane": inst.hastane,
                "kurumkodu": inst.kurumkodu,
                "il": inst.il,
                "oracle_sid": inst.oracle_sid,
                "enabled": inst.enabled,
                "rman_enabled": bool(inst.rman_enabled),
                "oracle_running": bool(runtime.get("oracle_running")),
                "oracle_status_label": runtime.get("oracle_status_label", "Kapali"),
                "backup_count": len(backup_service.list_backups(settings, inst, limit=500)),
                "last_dump": last_dump,
                "last_rman": last_rman,
                "log_mode": rman_rt.get("log_mode"),
                "archivelog": rman_rt.get("archivelog"),
                "last_report": last_reports.get(inst.id),
            }
        )

    server_info = get_server_info(settings, settings.first_instance(), oracle_stats_map=oracle_stats_map)
    host = {
        "hostname": server_info.get("hostname") or "",
        "cpu_cores": server_info.get("cpu_cores"),
        "load_avg": server_info.get("load_avg"),
        "mem_used_pct": server_info.get("mem_used_pct"),
        "mem_total_mb": server_info.get("mem_total_mb"),
        "mem_used_mb": server_info.get("mem_used_mb"),
        "disk_root_pct": server_info.get("disk_root_pct"),
        "disk_root_free_gb": server_info.get("disk_root_free_gb"),
        "disk_yedek_pct": server_info.get("disk_yedek_pct"),
        "disk_yedek_free_gb": server_info.get("disk_yedek_free_gb"),
    }

    return {
        "reported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "config_version": store.version,
        "instance_count": len(instances),
        "host": host,
        "backup_status": backup_service.backup_status(yedek_dir),
        "instances": instances,
    }
