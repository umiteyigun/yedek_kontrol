"""Oracle tablespace ve datafile sorgulari."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from app.services.oracle_probe import is_instance_running

logger = logging.getLogger(__name__)

TS_SCRIPT = "/yedek/config/oracle-tablespaces.sh"
DEFAULT_MAX_SIZE_MB = 32768  # 32 GB — yeni datafile onerisi


@dataclass
class TablespaceRow:
    name: str
    contents: str = ""
    status: str = ""
    size_gb: float = 0.0
    free_gb: float = 0.0
    used_gb: float = 0.0
    used_pct: int = 0
    max_gb: float = 0.0
    used_of_max_pct: int = 0
    block_size: int = 8192
    bigfile: str = "No"
    extent_management: str = ""
    allocation_type: str = ""
    segment_space_management: str = ""
    logging: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TablespaceRow:
        return cls(
            name=str(data.get("name") or ""),
            contents=str(data.get("contents") or ""),
            status=str(data.get("status") or ""),
            size_gb=float(data.get("size_gb") or 0),
            free_gb=float(data.get("free_gb") or 0),
            used_gb=float(data.get("used_gb") or 0),
            used_pct=int(data.get("used_pct") or 0),
            max_gb=float(data.get("max_gb") or 0),
            used_of_max_pct=int(data.get("used_of_max_pct") or 0),
            block_size=int(data.get("block_size") or 8192),
            bigfile=str(data.get("bigfile") or "No"),
            extent_management=str(data.get("extent_management") or ""),
            allocation_type=str(data.get("allocation_type") or ""),
            segment_space_management=str(data.get("segment_space_management") or ""),
            logging=str(data.get("logging") or ""),
        )


@dataclass
class DatafileRow:
    file_name: str
    file_id: int = 0
    usage_pct: int = 0
    size_gb: float = 0.0
    used_gb: float = 0.0
    free_gb: float = 0.0
    blocks: int = 0
    auto_extend: bool = False
    increment_mb: int = 0
    max_size: str = ""
    status: str = ""
    fragmentation_index: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DatafileRow:
        return cls(
            file_name=str(data.get("file_name") or ""),
            file_id=int(data.get("file_id") or 0),
            usage_pct=int(data.get("usage_pct") or 0),
            size_gb=float(data.get("size_gb") or 0),
            used_gb=float(data.get("used_gb") or 0),
            free_gb=float(data.get("free_gb") or 0),
            blocks=int(data.get("blocks") or 0),
            auto_extend=bool(data.get("auto_extend")),
            increment_mb=int(data.get("increment_mb") or data.get("increment_gb") or 0),
            max_size=str(data.get("max_size") or ""),
            status=str(data.get("status") or ""),
            fragmentation_index=float(data.get("fragmentation_index") or 0),
        )


def _run_script(mode: str, oracle_sid: str, tablespace: str = "", timeout: int = 120) -> dict[str, Any]:
    sid = (oracle_sid or "").strip()
    if not sid:
        return {"ok": False, "error": "Oracle SID bos"}
    if not is_instance_running(sid):
        return {"ok": False, "error": f"Oracle instance ayakta degil (SID={sid})"}

    args = ["nsenter", "-t", "1", "-m", "-p", "-i", "--", TS_SCRIPT, mode, sid]
    if tablespace:
        args.append(tablespace.upper())
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            return {"ok": False, "error": (proc.stderr or "Bos cevap").strip()[:300]}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"ok": False, "error": "Gecersiz JSON"}
        return data
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Tablespace sorgusu zaman asimi"}
    except json.JSONDecodeError:
        logger.warning("tablespace json parse hatasi: %s", raw[:200] if "raw" in dir() else "")
        return {"ok": False, "error": "JSON parse hatasi"}
    except FileNotFoundError:
        return {"ok": False, "error": "oracle-tablespaces.sh bulunamadi"}


def list_tablespaces(oracle_sid: str) -> tuple[list[TablespaceRow], str]:
    data = _run_script("list", oracle_sid)
    if not data.get("ok"):
        return [], str(data.get("error") or "Sorgu basarisiz")
    rows = [TablespaceRow.from_dict(item) for item in data.get("tablespaces") or []]
    return rows, ""


def list_datafiles(oracle_sid: str, tablespace: str) -> tuple[list[DatafileRow], str]:
    data = _run_script("datafiles", oracle_sid, tablespace)
    if not data.get("ok"):
        return [], str(data.get("error") or "Sorgu basarisiz")
    rows = [DatafileRow.from_dict(item) for item in data.get("datafiles") or []]
    return rows, ""


def suggest_next_datafile(tablespace: str, datafiles: list[DatafileRow]) -> dict[str, Any]:
    """Son datafile adina gore yeni dosya yolu oner."""
    ts = (tablespace or "").upper()
    default_size_mb = 1024
    num_suffix = re.compile(r"(\d+)(?=\.dbf$)", re.IGNORECASE)

    if not datafiles:
        return {
            "suggested_path": f"/u01/oradata/{ts.lower()}/{ts}_001.dbf",
            "size_mb": default_size_mb,
            "auto_extend": True,
            "next_mb": 100,
            "max_size": str(DEFAULT_MAX_SIZE_MB),
            "hint": "Bu tablespace icin datafile yok — 001 ile baslatildi",
            "based_on": "",
        }

    last = sorted(datafiles, key=lambda row: row.file_id)[-1]
    path = PurePosixPath(last.file_name)
    directory = str(path.parent)
    if directory and not directory.endswith("/"):
        directory += "/"

    name = path.name
    match = num_suffix.search(name)
    if match:
        width = len(match.group(1))
        next_num = int(match.group(1)) + 1
        next_str = str(next_num).zfill(width)
        new_name = name[: match.start(1)] + next_str + name[match.end(1) :]
        hint = f"Son datafile ({match.group(1)}) → onerilen numara {next_str}"
    else:
        stem = re.sub(r"\.dbf$", "", name, flags=re.IGNORECASE)
        new_name = f"{stem}001.dbf"
        hint = "Son dosyada numara yok — 001 eklendi"

    next_mb = last.increment_mb if last.increment_mb > 0 else 100
    return {
        "suggested_path": directory + new_name,
        "size_mb": default_size_mb,
        "auto_extend": last.auto_extend,
        "next_mb": next_mb,
        "max_size": str(DEFAULT_MAX_SIZE_MB),
        "hint": hint,
        "based_on": last.file_name,
    }


def _validate_datafile_path(file_path: str) -> str:
    path = (file_path or "").strip()
    if not path.startswith("/"):
        raise ValueError("Tam yol gerekli (/ ile baslamali)")
    if ".." in path.split("/"):
        raise ValueError("Gecersiz yol")
    if "'" in path or ";" in path or "\x00" in path:
        raise ValueError("Gecersiz karakter")
    if not path.lower().endswith(".dbf"):
        raise ValueError("Dosya adi .dbf ile bitmeli")
    return path


def add_datafile(
    oracle_sid: str,
    tablespace: str,
    file_path: str,
    size_mb: int,
    *,
    auto_extend: bool = True,
    next_mb: int = 100,
    max_size: str = "UNLIMITED",
) -> tuple[bool, str]:
    ts = (tablespace or "").strip().upper()
    if not ts:
        return False, "Tablespace adi gerekli"
    try:
        path = _validate_datafile_path(file_path)
    except ValueError as exc:
        return False, str(exc)

    size = int(size_mb)
    if size < 1 or size > 32767:
        return False, "Boyut 1-32767 MB arasi olmali"

    nxt = int(next_mb)
    if auto_extend and (nxt < 1 or nxt > 32767):
        return False, "NEXT 1-32767 MB arasi olmali"

    max_val = (max_size or "UNLIMITED").strip().upper()
    if max_val != "UNLIMITED":
        try:
            max_mb = int(float(max_val))
        except ValueError:
            return False, "MAXSIZE sayi veya UNLIMITED olmali"
        if max_mb < size:
            return False, "MAXSIZE baslangic boyutundan kucuk olamaz"
        max_val = str(max_mb)

    sid = (oracle_sid or "").strip()
    if not sid:
        return False, "Oracle SID bos"
    if not is_instance_running(sid):
        return False, f"Oracle instance ayakta degil (SID={sid})"

    args = [
        "nsenter",
        "-t",
        "1",
        "-m",
        "-p",
        "-i",
        "--",
        TS_SCRIPT,
        "add",
        sid,
        ts,
        path,
        str(size),
        "yes" if auto_extend else "no",
        str(nxt if auto_extend else 0),
        max_val if auto_extend else "0",
    ]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            return False, (proc.stderr or "Bos cevap").strip()[:300]
        data = json.loads(raw)
        if not isinstance(data, dict):
            return False, "Gecersiz JSON"
        if data.get("ok"):
            return True, str(data.get("message") or "Datafile eklendi")
        return False, str(data.get("error") or "Ekleme basarisiz")
    except subprocess.TimeoutExpired:
        return False, "Datafile ekleme zaman asimi"
    except json.JSONDecodeError:
        return False, "JSON parse hatasi"
    except FileNotFoundError:
        return False, "oracle-tablespaces.sh bulunamadi"
