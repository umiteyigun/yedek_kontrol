"""Oracle tablespace ve datafile sorgulari."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any

from app.services.oracle_probe import is_instance_running

logger = logging.getLogger(__name__)

TS_SCRIPT = "/yedek/config/oracle-tablespaces.sh"


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
    increment_gb: float = 0.0
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
            increment_gb=float(data.get("increment_gb") or 0),
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
