import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Request

from app.auth import can_manage_settings, get_current_role, get_current_user
from app.config.models import InstanceSettings, YedekSettings
from app.config.store import mask_secret
from app.services import backups as backup_service
from app.services.oracle_probe import instance_runtime_map
from app.services.permissions import get_request_permissions, nav_flags
from app.services.server_info import collect_all_instance_oracle_stats, get_server_info
from app.services.server_time import list_host_timezones, merge_clock_into_server_info


def active_instance(settings: YedekSettings, request: Request) -> InstanceSettings | None:
    return settings.get_instance(request.query_params.get("instance"))


def instance_view(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    data = inst.model_dump()
    data["display_name"] = inst.display_name()
    data["directorydizini"] = inst.effective_directorydizini(settings.yedek_dir)
    data["masked_ftp_pass"] = mask_secret(inst.localftppass)
    data["masked_backup_protect_pass"] = mask_secret(inst.backup_protect_pass)
    return data


def page_context(
    request: Request,
    settings: YedekSettings | dict[str, Any],
    message: str = "",
    error: str = "",
) -> dict[str, Any]:
    if isinstance(settings, dict):
        settings = YedekSettings.model_validate(settings)

    yedek_dir = Path(request.app.state.yedek_dir)
    store = request.app.state.store
    role = get_current_role(request)
    active = active_instance(settings, request) or settings.first_instance()
    active_view = instance_view(settings, active) if active else {}

    backup_rows: list[dict[str, Any]] = []
    all_backups_count = 0
    total_backup_mb = 0.0
    if active:
        items = backup_service.list_backups(settings, active, limit=10)
        backup_rows = [
            {
                "name": item.archive_name,
                "size_mb": item.size_mb,
                "mtime": item.mtime,
                "mtime_short": item.mtime[:5],
                "instance_id": item.instance_id,
            }
            for item in items
        ]
        all_items = backup_service.list_backups(settings, active, limit=500)
        all_backups_count = len(all_items)
        total_backup_mb = round(sum(item.size_mb for item in all_items), 1)

    max_bar = max((b["size_mb"] for b in backup_rows), default=1) or 1
    for row in backup_rows:
        row["bar_pct"] = max(8, int(row["size_mb"] / max_bar * 100))

    disk_root = disk_yedek = "?"
    disk_root_pct = disk_yedek_pct = 0
    try:
        root = shutil.disk_usage("/")
        disk_root_pct = int(root.used * 100 / root.total)
        disk_root = f"{disk_root_pct}%"
    except OSError:
        pass
    try:
        target_dir = (
            Path(active.effective_directorydizini(settings.yedek_dir))
            if active
            else yedek_dir
        )
        usage_path = target_dir if target_dir.exists() else target_dir.parent
        yedek = shutil.disk_usage(str(usage_path))
        disk_yedek_pct = int(yedek.used * 100 / yedek.total)
        disk_yedek = f"{disk_yedek_pct}%"
    except OSError:
        try:
            yedek = shutil.disk_usage(str(yedek_dir))
            disk_yedek_pct = int(yedek.used * 100 / yedek.total)
            disk_yedek = f"{disk_yedek_pct}%"
        except OSError:
            pass

    applied = store.applied_at.strftime("%d.%m.%Y %H:%M:%S") if store.applied_at else "-"
    qp_error = request.query_params.get("error", "")
    instance_views = [instance_view(settings, inst) for inst in settings.instances]
    runtime_map = instance_runtime_map(settings.model_dump())
    oracle_stats_map = collect_all_instance_oracle_stats(settings, runtime_map)

    instance_summaries: list[dict[str, Any]] = []
    total_all_backups = 0
    total_all_mb = 0.0
    oracle_open_count = 0
    oracle_closed_count = 0
    for inst in settings.instances:
        view = instance_view(settings, inst)
        runtime = runtime_map.get(inst.id, {})
        if runtime.get("oracle_running"):
            oracle_open_count += 1
        else:
            oracle_closed_count += 1
        items = backup_service.list_backups(settings, inst, limit=500)
        last = items[0] if items else None
        inst_mb = round(sum(i.size_mb for i in items), 1)
        total_all_backups += len(items)
        total_all_mb += inst_mb
        instance_summaries.append(
            {
                **view,
                **oracle_stats_map.get(inst.id, {}),
                "backup_count": len(items),
                "total_backup_mb": inst_mb,
                "oracle_running": bool(runtime.get("oracle_running")),
                "oracle_status": runtime.get("oracle_status", "kapali"),
                "oracle_status_label": runtime.get("oracle_status_label", "Kapali"),
                "last_backup": (
                    {
                        "name": last.archive_name,
                        "mtime": last.mtime,
                        "size_mb": last.size_mb,
                    }
                    if last
                    else None
                ),
                "has_backup": bool(items),
            }
        )

    if active:
        active_runtime = runtime_map.get(active.id, {})
        active_view = {
            **active_view,
            **oracle_stats_map.get(active.id, {}),
            "oracle_running": bool(active_runtime.get("oracle_running")),
            "oracle_status": active_runtime.get("oracle_status", "kapali"),
            "oracle_status_label": active_runtime.get("oracle_status_label", "Kapali"),
        }

    enriched_instances = []
    for inst in instance_views:
        runtime = runtime_map.get(inst["id"], {})
        enriched_instances.append(
            {
                **inst,
                "oracle_running": bool(runtime.get("oracle_running")),
                "oracle_status_label": runtime.get("oracle_status_label", "Kapali"),
            }
        )

    server_info = get_server_info(settings, active, oracle_stats_map=oracle_stats_map)
    server_info = merge_clock_into_server_info(server_info)
    is_admin = can_manage_settings(request)
    perms = get_request_permissions(request)
    flags = nav_flags(request)

    return {
        "request": request,
        "user": get_current_user(request),
        "role": role,
        "perms": perms,
        "can_settings": is_admin,
        "can_admin": is_admin,
        **flags,
        "role_label": "Tam Yetki" if role == "full" else "Yedek Operatoru",
        "settings": settings.model_dump(),
        "active_instance": active_view,
        "active_instance_id": active.id if active else "",
        "instances": enriched_instances,
        "instance_summaries": instance_summaries,
        "instance_count": len(settings.instances),
        "oracle_open_count": oracle_open_count,
        "oracle_closed_count": oracle_closed_count,
        "total_all_backups": total_all_backups,
        "total_all_backup_mb": round(total_all_mb, 1),
        "config_version": store.version,
        "applied_at": applied,
        "backups": backup_rows,
        "backup_count": all_backups_count,
        "total_backup_mb": total_backup_mb,
        "last_backup": backup_rows[0] if backup_rows else None,
        "disk_root": disk_root,
        "disk_yedek": disk_yedek,
        "disk_root_pct": disk_root_pct,
        "disk_yedek_pct": disk_yedek_pct,
        "disk_root_free": 100 - disk_root_pct,
        "disk_yedek_free": 100 - disk_yedek_pct,
        "health_ok": disk_root_pct < 90 and disk_yedek_pct < 90,
        "server_info": server_info,
        "server_timezone": server_info.get("timezone") if server_info.get("timezone") not in ("", "—") else settings.server_timezone,
        "timezone_options": list_host_timezones() if is_admin else (),
        "message": message,
        "error": error or qp_error,
    }
