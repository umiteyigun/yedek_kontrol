"""Uzak FTP sunucusunda dizin listeleme ve dosya silme (ayarlar paneli)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from ftplib import FTP, error_perm
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 15
PROTECTED_NEWEST_COUNT = 5
BACKUP_DATE_RE = re.compile(r"(?:GUNLUKYEDEK|HAFTALIKYEDEK)(\d{10})", re.IGNORECASE)
FILENAME_INLINE_DATE_RE = re.compile(r"-(\d{12,14})-")


@dataclass(frozen=True)
class FtpEntry:
    name: str
    entry_type: str
    size: int
    modified: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.entry_type,
            "size": self.size,
            "modified": self.modified,
        }


def parse_host(host: str, default_port: int = 21) -> tuple[str, int]:
    value = host.strip()
    if not value:
        return "", default_port
    if ":" in value:
        hostname, port_raw = value.rsplit(":", 1)
        if port_raw.isdigit():
            return hostname.strip(), int(port_raw)
    return value, default_port


def _normalize_path(path: str) -> str:
    cleaned = (path or "/").strip().replace("\\", "/")
    if not cleaned or cleaned == ".":
        return "/"
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    parts = [part for part in cleaned.split("/") if part and part != "."]
    return "/" + "/".join(parts) if parts else "/"


def _cwd_to(ftp: FTP, path: str) -> str:
    target = _normalize_path(path)
    if target == "/":
        ftp.cwd("/")
        return ftp.pwd()

    try:
        ftp.cwd(target)
        return ftp.pwd()
    except error_perm:
        ftp.cwd("/")
        for part in [p for p in target.split("/") if p]:
            ftp.cwd(part)
        return ftp.pwd()


def _entry_type_from_facts(facts: dict[str, str]) -> str:
    raw = (facts.get("type") or "").lower()
    if raw in {"dir", "cdir", "pdir"}:
        return "dir"
    return "file"


def _parse_yyyymmddhh(value: str) -> int:
    try:
        return int(datetime.strptime(value[:10], "%Y%m%d%H").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def _parse_mlsd_modify(modified: str) -> int:
    try:
        return int(datetime.strptime(modified[:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        return 0


def _file_timestamp(name: str, modified: str) -> int:
    """Dosya tarihi: once FTP degistirilme, sonra dosya adindaki tarih."""
    if modified:
        ts = _parse_mlsd_modify(modified)
        if ts:
            return ts
    inline = FILENAME_INLINE_DATE_RE.search(name)
    if inline:
        ts = _parse_mlsd_modify(inline.group(1))
        if ts:
            return ts
    match = BACKUP_DATE_RE.search(name)
    if match:
        ts = _parse_yyyymmddhh(match.group(1))
        if ts:
            return ts
    return 0


def _format_modified(modified: str, sort_ts: int) -> str:
    if modified and len(modified) >= 14:
        try:
            dt = datetime.strptime(modified[:14], "%Y%m%d%H%M%S")
            return dt.strftime("%d.%m.%Y %H:%M")
        except ValueError:
            pass
    if sort_ts > 0:
        return datetime.fromtimestamp(sort_ts, tz=timezone.utc).strftime("%d.%m.%Y %H:%M")
    return "—"


def _is_backup_file(name: str) -> bool:
    """Yedek arsivi: Oracle dump, YEDEK adli, Full.zip ve tarihli zip yedekleri."""
    lower = name.lower()
    if lower.endswith(".dmp.gz") or lower.endswith(".dmp"):
        return True
    if "yedek" in lower:
        return True
    if lower.endswith("-full.zip"):
        return True
    if lower.endswith(".zip") and FILENAME_INLINE_DATE_RE.search(name):
        return True
    if lower.endswith(".tar.gz") or lower.endswith(".bak"):
        return True
    return False


def _mdtm(ftp: FTP, name: str) -> str:
    try:
        resp = ftp.sendcmd(f"MDTM {name}")
        parts = resp.split(maxsplit=1)
        if len(parts) == 2 and len(parts[1]) >= 14:
            return parts[1][:14]
    except (error_perm, OSError):
        pass
    return ""


def _list_with_mlsd(ftp: FTP) -> list[FtpEntry]:
    entries: list[FtpEntry] = []
    for name, facts in ftp.mlsd():
        if name in {".", ".."}:
            continue
        entries.append(
            FtpEntry(
                name=name,
                entry_type=_entry_type_from_facts(facts),
                size=int(facts.get("size") or 0),
                modified=facts.get("modify", ""),
            )
        )
    return entries


def _hydrate_file_dates(ftp: FTP, entries: list[FtpEntry]) -> list[FtpEntry]:
    """MLSD/NLST tarih vermeyen dosyalar icin MDTM ile degistirilme tarihini tamamla."""
    hydrated: list[FtpEntry] = []
    for entry in entries:
        if entry.entry_type != "file":
            hydrated.append(entry)
            continue
        if entry.modified:
            hydrated.append(entry)
            continue
        modified = _mdtm(ftp, entry.name)
        hydrated.append(
            FtpEntry(
                name=entry.name,
                entry_type=entry.entry_type,
                size=entry.size,
                modified=modified,
            )
        )
    return hydrated


def _list_directory_entries(ftp: FTP) -> list[FtpEntry]:
    try:
        entries = _list_with_mlsd(ftp)
    except (error_perm, AttributeError, OSError) as exc:
        logger.debug("MLSD desteklenmiyor, NLST kullaniliyor: %s", exc)
        entries = _list_with_nlst(ftp)
    return _hydrate_file_dates(ftp, entries)


def _list_with_nlst(ftp: FTP) -> list[FtpEntry]:
    entries: list[FtpEntry] = []
    names: list[str] = []
    ftp.retrlines("NLST", names.append)
    for name in names:
        if name in {".", ".."}:
            continue
        entry_type = "file"
        size = 0
        modified = ""
        try:
            size = int(ftp.size(name) or 0)
        except (error_perm, TypeError, ValueError):
            entry_type = "dir"
            size = 0
        if entry_type == "file":
            modified = _mdtm(ftp, name)
        entries.append(FtpEntry(name=name, entry_type=entry_type, size=size, modified=modified))
    return entries


def _enrich_entries(entries: list[FtpEntry], margin_pct: int) -> tuple[list[dict[str, object]], dict[str, object]]:
    backup_files = [entry for entry in entries if entry.entry_type == "file" and _is_backup_file(entry.name)]

    dated_backups: list[tuple[FtpEntry, int]] = []
    for entry in backup_files:
        ts = _file_timestamp(entry.name, entry.modified)
        if ts > 0:
            dated_backups.append((entry, ts))
    dated_backups.sort(key=lambda item: item[1], reverse=True)

    latest_size = 0
    for entry, _ in dated_backups:
        if entry.size > 0:
            latest_size = entry.size
            break

    protected_names = {entry.name for entry, _ in dated_backups[:PROTECTED_NEWEST_COUNT]}
    threshold = int(latest_size * max(0.05, 1 - margin_pct / 100)) if latest_size > 0 else 0

    dirs: list[dict[str, object]] = []
    files: list[dict[str, object]] = []

    for entry in entries:
        sort_ts = _file_timestamp(entry.name, entry.modified) if entry.entry_type == "file" else 0
        zero_size = entry.entry_type == "file" and entry.size == 0
        is_backup = _is_backup_file(entry.name)
        protected = entry.name in protected_names
        suspicious = (
            entry.entry_type == "file"
            and is_backup
            and entry.size > 0
            and latest_size > 0
            and not protected
            and entry.size < threshold
        )
        row: dict[str, object] = {
            **entry.as_dict(),
            "is_backup": is_backup,
            "sort_ts": sort_ts,
            "modified_display": _format_modified(entry.modified, sort_ts),
            "protected": protected,
            "zero_size": zero_size,
            "suspicious": suspicious,
            "auto_delete": zero_size,
            "deletable": entry.entry_type == "file" and not protected,
        }
        if entry.entry_type == "dir":
            dirs.append(row)
        else:
            files.append(row)

    dirs.sort(key=lambda item: str(item["name"]).lower())
    files.sort(key=lambda item: (int(item["sort_ts"] or 0), str(item["name"]).lower()))

    analysis = {
        "latest_size": latest_size,
        "margin_pct": margin_pct,
        "protected": sorted(protected_names),
        "protected_count": PROTECTED_NEWEST_COUNT,
        "size_threshold": threshold,
        "protection_by": "file_date",
    }
    return dirs + files, analysis


def _connect(host: str, user: str, password: str, path: str) -> tuple[FTP, str]:
    hostname, port = parse_host(host)
    if not hostname:
        raise ValueError("FTP sunucu adresi bos")
    if not user:
        raise ValueError("FTP kullanici adi bos")

    ftp = FTP(timeout=DEFAULT_TIMEOUT)
    ftp.connect(hostname, port)
    ftp.login(user, password)
    ftp.set_pasv(True)
    current_path = _cwd_to(ftp, path)
    return ftp, current_path


def browse_directory(
    host: str,
    user: str,
    password: str,
    path: str = "/",
    *,
    margin_pct: int = 25,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, object]:
    """FTP sunucusuna baglanip dizin icerigini dondur."""
    hostname, port = parse_host(host)
    ftp = FTP(timeout=timeout)
    try:
        ftp.connect(hostname, port)
        ftp.login(user, password)
        ftp.set_pasv(True)
        current_path = _cwd_to(ftp, path)

        entries = _list_directory_entries(ftp)

        enriched, analysis = _enrich_entries(entries, margin_pct)
        return {
            "host": hostname,
            "port": port,
            "path": current_path,
            "entries": enriched,
            "analysis": analysis,
        }
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass


def delete_files(
    host: str,
    user: str,
    password: str,
    path: str,
    filenames: list[str],
    *,
    margin_pct: int = 25,
) -> dict[str, object]:
    """Secili dosyalari sil; son 5 yedek ve korunan dosyalara izin verme."""
    cleaned_names = [name.strip() for name in filenames if name.strip()]
    if not cleaned_names:
        raise ValueError("Silinecek dosya secilmedi")

    for name in cleaned_names:
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise ValueError(f"Gecersiz dosya adi: {name}")

    ftp, current_path = _connect(host, user, password, path)
    try:
        entries = _list_directory_entries(ftp)

        enriched, analysis = _enrich_entries(entries, margin_pct)
        known = {str(row["name"]): row for row in enriched if row["type"] == "file"}

        deleted: list[str] = []
        for name in cleaned_names:
            row = known.get(name)
            if not row:
                raise ValueError(f"Dizinde bulunamadi: {name}")
            if row.get("protected"):
                raise ValueError(f"Son {PROTECTED_NEWEST_COUNT} yedek korunuyor: {name}")

        for name in cleaned_names:
            ftp.delete(name)
            deleted.append(name)

        return {
            "path": current_path,
            "deleted": deleted,
            "count": len(deleted),
        }
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass


def upload_files(
    host: str,
    user: str,
    password: str,
    path: str,
    uploads: list[tuple[Path, str]],
) -> dict[str, object]:
    """Yerel yedek dosyalarini uzak FTP dizinine yukler."""
    if not uploads:
        raise ValueError("Yuklenecek dosya secilmedi")

    ftp, current_path = _connect(host, user, password, path)
    uploaded: list[str] = []
    try:
        for local_path, remote_name in uploads:
            if not local_path.is_file():
                raise ValueError(f"Yerel dosya bulunamadi: {local_path.name}")
            if "/" in remote_name or "\\" in remote_name or remote_name in {".", ".."}:
                raise ValueError(f"Gecersiz uzak dosya adi: {remote_name}")
            with local_path.open("rb") as handle:
                ftp.storbinary(f"STOR {remote_name}", handle)
            uploaded.append(remote_name)
        return {
            "path": current_path,
            "uploaded": uploaded,
            "count": len(uploaded),
        }
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass
