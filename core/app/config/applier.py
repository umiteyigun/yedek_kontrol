import json
import logging
import shlex
from pathlib import Path

from app.config.models import InstanceSettings, YedekSettings

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"


def _render_template(name: str, settings: YedekSettings) -> str:
    content = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
    values = settings.model_dump()
    for key in (
        "schemas",
        "guid_key",
        "yedek_kodu",
        "hastane",
        "kurumkodu",
        "directory",
        "directorydizini",
        "oracle_sid",
    ):
        values[key] = getattr(settings, key, "")
    for key, value in values.items():
        if key == "instances":
            continue
        content = content.replace("{{ " + key + " }}", str(value))
    return content


def _render_instance_shell(inst: InstanceSettings, settings: YedekSettings) -> str:
    directorydizini = inst.effective_directorydizini(settings.yedek_dir)
    ftp_ip, ftp_user, ftp_pass = inst.effective_ftp(settings)
    lines = [
        f'# Instance: {inst.id} ({inst.display_name()}) - otomatik uretildi',
        f'INSTANCE_ID={shlex.quote(inst.id)}',
        f'label={shlex.quote(inst.label)}',
        f'backup_prefix={shlex.quote(inst.backup_file_prefix())}',
        f'ORACLE_SID={shlex.quote(inst.oracle_sid)}',
        f'hastane={shlex.quote(inst.hastane)}',
        f'il={shlex.quote(inst.il)}',
        f'schemas={shlex.quote(inst.schemas)}',
        f'kurumkodu={shlex.quote(inst.kurumkodu)}',
        f'directory={shlex.quote(inst.directory)}',
        f'directorydizini={shlex.quote(directorydizini)}',
        f'hostname={shlex.quote(settings.hostname)}',
        f'yedek_kodu={shlex.quote(inst.yedek_kodu)}',
        f'guid_key={shlex.quote(inst.guid_key)}',
        f'localftpip={shlex.quote(ftp_ip)}',
        f"localftpuser={shlex.quote(ftp_user)}",
        f"localftppass={shlex.quote(ftp_pass)}",
        f'localftpdir={shlex.quote(inst.localftpdir or "/")}',
        f'mail_notify={1 if settings.mail_notify else 0}',
        f"backup_protect_mode={shlex.quote(inst.backup_protect_mode)}",
        f"backup_protect_pass={shlex.quote(inst.backup_protect_pass)}",
        f'backup_split_enabled={1 if inst.backup_split_enabled else 0}',
        f"backup_split_size_mb={inst.backup_split_size_mb}",
        f'rman_enabled={1 if inst.rman_enabled else 0}',
        f"rman_dest={shlex.quote(inst.effective_rman_dest())}",
        f'rman_archivelog_backup={1 if inst.rman_archivelog_backup else 0}',
        f"rman_retention_days={inst.rman_retention_days}",
        f"rman_channels={inst.rman_channels}",
        f'rman_compression={1 if inst.rman_compression else 0}',
        "",
    ]
    return "\n".join(lines)


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


class ConfigApplier:
    """Host scriptlerini aninda yazar; container restart gerekmez."""

    def __init__(
        self,
        host_output_dir: Path,
        generated_dir: Path,
    ) -> None:
        self.host_output_dir = host_output_dir
        self.generated_dir = generated_dir

    def apply(self, settings: YedekSettings) -> list[str]:
        written: list[str] = []
        self.generated_dir.mkdir(parents=True, exist_ok=True)
        self.host_output_dir.mkdir(parents=True, exist_ok=True)

        files = {
            "yedekconfig.sh": _render_template("yedekconfig.sh.tpl", settings),
            "yedek-params.sh": _render_template("yedek-params.sh.tpl", settings),
        }

        for filename, content in files.items():
            host_path = self.host_output_dir / filename
            _write_executable(host_path, content)
            written.append(str(host_path))
            audit_path = self.generated_dir / filename
            audit_path.write_text(content, encoding="utf-8")

        instances_dir = self.host_output_dir / "instances"
        audit_instances_dir = self.generated_dir / "instances"
        instances_dir.mkdir(parents=True, exist_ok=True)
        audit_instances_dir.mkdir(parents=True, exist_ok=True)

        enabled_ids: list[str] = []
        instances_payload: list[dict] = []
        for inst in settings.instances:
            inst_shell = _render_instance_shell(inst, settings)
            inst_path = instances_dir / f"{inst.id}.sh"
            _write_executable(inst_path, inst_shell)
            written.append(str(inst_path))
            (audit_instances_dir / f"{inst.id}.sh").write_text(inst_shell, encoding="utf-8")

            payload = inst.model_dump()
            payload["directorydizini"] = inst.effective_directorydizini(settings.yedek_dir)
            payload["display_name"] = inst.display_name()
            instances_payload.append(payload)
            if inst.enabled:
                enabled_ids.append(inst.id)

        list_content = "\n".join(enabled_ids) + ("\n" if enabled_ids else "")
        for base in (self.host_output_dir, self.generated_dir):
            list_path = base / "instances.list"
            list_path.write_text(list_content, encoding="utf-8")
            written.append(str(list_path))

        instances_json = {
            "oracle_ver": settings.oracle_ver,
            "hostname": settings.hostname,
            "yedek_dir": settings.yedek_dir,
            "core_port": settings.core_port,
            "instances": instances_payload,
        }
        for base in (self.host_output_dir, self.generated_dir):
            json_path = base / "instances.json"
            json_path.write_text(json.dumps(instances_json, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(str(json_path))

        schedule_json = {
            "timezone": settings.server_timezone or "Europe/Istanbul",
            "instances": {
                inst.id: {
                    "enabled": inst.enabled,
                    "oracle_sid": inst.oracle_sid,
                    "display_name": inst.display_name(),
                    "rules": [rule.model_dump() for rule in inst.schedules],
                    "rman_enabled": inst.rman_enabled,
                    "rman_dest": inst.effective_rman_dest(),
                    "rman_rules": [rule.model_dump() for rule in inst.rman_schedules],
                }
                for inst in settings.instances
            },
        }
        disk_guard_json = {
            "yedek_dir": settings.yedek_dir,
            "max_usage_pct": settings.backup_disk_max_pct,
            "min_free_gb": settings.backup_disk_min_free_gb,
            "reserve_gb": settings.backup_disk_reserve_gb,
            "margin_pct": settings.backup_size_margin_pct,
            "estimate_gunluk_gb": 3.0,
            "estimate_haftalik_gb": 10.0,
            "estimate_rman_full_gb": 25.0,
            "estimate_rman_incr_gb": 5.0,
            "weekly_size_factor": 1.35,
        }
        for base in (self.host_output_dir, self.generated_dir):
            schedule_path = base / "schedule.json"
            schedule_path.write_text(
                json.dumps(schedule_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            written.append(str(schedule_path))
            guard_path = base / "disk-guard.json"
            guard_path.write_text(
                json.dumps(disk_guard_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            written.append(str(guard_path))

        stale = {path.stem for path in instances_dir.glob("*.sh")} - {inst.id for inst in settings.instances}
        for stale_id in stale:
            (instances_dir / f"{stale_id}.sh").unlink(missing_ok=True)
            (audit_instances_dir / f"{stale_id}.sh").unlink(missing_ok=True)

        logger.info(
            "Host config uygulandi: %s instance(s) [%s]",
            len(settings.instances),
            ", ".join(enabled_ids) or "-",
        )
        return written
