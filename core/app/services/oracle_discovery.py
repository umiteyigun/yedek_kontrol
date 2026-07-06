import logging
import re
import subprocess
import uuid
from dataclasses import dataclass

from app.config.constants import ORACLE_DIRECTORY_NAME
from app.config.models import slugify

logger = logging.getLogger(__name__)

ORATAB_PATH = "/etc/oratab"


@dataclass(frozen=True)
class OratabEntry:
    oracle_sid: str
    oracle_home: str
    autostart: str


def parse_oratab(content: str) -> list[OratabEntry]:
    """oratab satirlarini oku; ASM/APX (+) ve yorumlari atla."""
    entries: list[OratabEntry] = []
    seen: set[str] = set()
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) < 3:
            continue
        sid = parts[0].strip()
        if not sid or sid.startswith("+"):
            continue
        sid_key = sid.lower()
        if sid_key in seen:
            continue
        seen.add(sid_key)
        entries.append(
            OratabEntry(
                oracle_sid=sid,
                oracle_home=parts[1].strip(),
                autostart=parts[2].strip().upper(),
            )
        )
    return entries


def oracle_ver_from_home(oracle_home: str) -> str:
    match = re.search(r"/product/([^/]+)/", oracle_home)
    return match.group(1) if match else ""


def read_oratab_from_host() -> tuple[int, str, str]:
    cmd = ["nsenter", "-t", "1", "-m", "--", "cat", ORATAB_PATH]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
        return proc.returncode, proc.stdout, proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "oratab okuma zaman asimi"
    except FileNotFoundError:
        return 127, "", "nsenter bulunamadi"


def discover_oratab_entries() -> list[OratabEntry]:
    code, stdout, stderr = read_oratab_from_host()
    if code != 0:
        logger.warning("oratab okunamadi (%s): %s", code, stderr or "bilinmeyen hata")
        return []
    entries = parse_oratab(stdout)
    logger.info("oratab: %s database instance bulundu", len(entries))
    return entries


def unique_instance_id(sid: str, existing_ids: set[str]) -> str:
    base = slugify(sid) or "instance"
    candidate = base
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def build_instance_from_sid(sid: str, yedek_dir: str, instance_id: str) -> dict[str, object]:
    clean_dir = yedek_dir.rstrip("/") or "/yedek/orayedek"
    return {
        "id": instance_id,
        "enabled": False,
        "label": sid,
        "hastane": sid,
        "il": "",
        "password": "",
        "schemas": "SYSTEM",
        "kurumkodu": "",
        "directory": ORACLE_DIRECTORY_NAME,
        "directorydizini": f"{clean_dir}/",
        "oracle_sid": sid,
        "yedek_kodu": "Hbys",
        "guid_key": str(uuid.uuid4()),
        "localftpip": "",
        "localftpuser": "",
        "localftppass": "",
        "localftpdir": "/",
        "ftp_upload_enabled": False,
        "localftpip2": "",
        "localftpuser2": "",
        "localftppass2": "",
        "localftpdir2": "/",
        "ftp2_upload_enabled": False,
        "retention_days": 2,
        "backup_protect_mode": "gzip",
        "backup_protect_pass": "",
        "backup_split_enabled": False,
        "backup_split_size_mb": 2048,
    }


def sync_instances_from_oratab(settings_dict: dict) -> tuple[dict, list[str]]:
    """Mevcut ayarlara oratab'taki eksik SID'leri ekle; mevcut kurumlari koru."""
    discovered = discover_oratab_entries()
    if not discovered:
        return settings_dict, []

    instances = [dict(item) for item in settings_dict.get("instances", [])]
    known_sids = {str(item.get("oracle_sid", "")).lower() for item in instances if item.get("oracle_sid")}
    existing_ids = {str(item.get("id", "")) for item in instances if item.get("id")}
    yedek_dir = str(settings_dict.get("yedek_dir", "/yedek/orayedek"))
    added: list[str] = []

    for entry in discovered:
        sid_key = entry.oracle_sid.lower()
        if sid_key in known_sids:
            continue
        instance_id = unique_instance_id(entry.oracle_sid, existing_ids)
        existing_ids.add(instance_id)
        known_sids.add(sid_key)
        instances.append(build_instance_from_sid(entry.oracle_sid, yedek_dir, instance_id))
        added.append(entry.oracle_sid)

    if not added:
        return settings_dict, []

    updated = dict(settings_dict)
    updated["instances"] = instances

    if not updated.get("oracle_ver"):
        for entry in discovered:
            ver = oracle_ver_from_home(entry.oracle_home)
            if ver:
                updated["oracle_ver"] = ver
                break

    logger.info("oratab senkron: yeni instance eklendi: %s", ", ".join(added))
    return updated, added
