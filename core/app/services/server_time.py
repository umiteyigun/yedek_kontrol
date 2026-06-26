"""Host sunucu saati ve saat dilimi."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from typing import Any

logger = logging.getLogger(__name__)

HOST_TIMEZONE_SCRIPT = "/yedek/config/host-timezone.sh"
TZ_NAME_RE = re.compile(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")
DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

_TZ_LIST_CACHE: tuple[float, tuple[str, ...]] = (0.0, ())
_TZ_LIST_TTL = 3600


def _run_host_script(script_path: str, *args: str, timeout: int = 30) -> tuple[int, str, str]:
    cmd = ["nsenter", "-t", "1", "-m", "-p", "--", script_path, *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "host script zaman asimi"
    except FileNotFoundError:
        return 127, "", "nsenter bulunamadi"


def _parse_json(stdout: str) -> dict[str, Any]:
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


def _empty_clock() -> dict[str, Any]:
    return {
        "clock_ok": False,
        "clock_error": "",
        "clock_epoch": 0,
        "clock_datetime": "—",
        "clock_date": "—",
        "clock_time": "—:—:—",
        "timezone": "—",
        "timezone_label": "—",
        "utc_offset": "",
        "ntp_synchronized": "",
        "rtc_in_local_tz": "",
    }


def normalize_clock_payload(data: dict[str, Any]) -> dict[str, Any]:
    offset = str(data.get("utc_offset") or "")
    if offset and len(offset) == 5 and offset[0] in "+-":
        offset = f"{offset[:3]}:{offset[3:]}"
    tz = str(data.get("timezone") or "—")
    label = str(data.get("timezone_label") or tz)
    return {
        "clock_ok": bool(data.get("ok")),
        "clock_error": str(data.get("error") or ""),
        "clock_epoch": int(data.get("clock_epoch") or 0),
        "clock_datetime": str(data.get("clock_datetime") or "—"),
        "clock_date": str(data.get("clock_date") or "—"),
        "clock_time": str(data.get("clock_time") or "—:—:—"),
        "timezone": tz,
        "timezone_label": label,
        "utc_offset": offset,
        "ntp_synchronized": str(data.get("ntp_synchronized") or ""),
        "rtc_in_local_tz": str(data.get("rtc_in_local_tz") or ""),
    }


def collect_host_clock() -> dict[str, Any]:
    code, stdout, stderr = _run_host_script(HOST_TIMEZONE_SCRIPT, "get")
    data = _parse_json(stdout)
    if not data.get("ok"):
        logger.warning("host-timezone get basarisiz (%s): %s", code, stderr or stdout[:200])
        result = _empty_clock()
        result["clock_error"] = str(data.get("error") or stderr or "Saat bilgisi okunamadi")
        return result
    return normalize_clock_payload(data)


def list_host_timezones(*, force_refresh: bool = False) -> tuple[str, ...]:
    global _TZ_LIST_CACHE
    now = time.time()
    if not force_refresh and _TZ_LIST_CACHE[1] and now - _TZ_LIST_CACHE[0] < _TZ_LIST_TTL:
        return _TZ_LIST_CACHE[1]

    code, stdout, stderr = _run_host_script(HOST_TIMEZONE_SCRIPT, "list-timezones", timeout=60)
    data = _parse_json(stdout)
    zones = data.get("timezones") if data.get("ok") else []
    if not isinstance(zones, list) or not zones:
        logger.warning("host timezone listesi alinamadi (%s): %s", code, stderr or stdout[:200])
        zones = ["UTC", "Europe/Istanbul"]

    clean = tuple(str(item).strip() for item in zones if str(item).strip())
    _TZ_LIST_CACHE = (now, clean)
    return clean


def invalidate_timezone_cache() -> None:
    global _TZ_LIST_CACHE
    _TZ_LIST_CACHE = (0.0, ())


def _validate_timezone_name(timezone: str) -> tuple[bool, str]:
    clean = (timezone or "").strip()
    if not TZ_NAME_RE.fullmatch(clean):
        return False, "Gecersiz saat dilimi adi"
    return True, clean


def set_host_timezone(timezone: str) -> tuple[bool, str, dict[str, Any]]:
    ok, clean_or_msg = _validate_timezone_name(timezone)
    if not ok:
        return False, clean_or_msg, {}

    clean = clean_or_msg
    code, stdout, stderr = _run_host_script(HOST_TIMEZONE_SCRIPT, "set", clean)
    data = _parse_json(stdout)
    if not data.get("ok"):
        message = str(data.get("error") or stderr or "Saat dilimi ayarlanamadi")
        logger.warning("host-timezone set basarisiz (%s): %s", code, message)
        return False, message, {}

    invalidate_timezone_cache()
    return True, f"Saat dilimi guncellendi: {clean}", normalize_clock_payload(data)


def _normalize_time_value(time_value: str) -> str:
    clean = (time_value or "").strip()
    if TIME_RE.fullmatch(clean):
        if clean.count(":") == 1:
            return f"{clean}:00"
        return clean
    raise ValueError("Saat formati gecersiz (HH:MM veya HH:MM:SS)")


def set_host_clock(
    clock_date: str,
    clock_time: str,
    timezone: str,
) -> tuple[bool, str, dict[str, Any]]:
    date_clean = (clock_date or "").strip()
    if not DATE_RE.fullmatch(date_clean):
        return False, "Tarih formati gecersiz (YYYY-MM-DD)", {}

    try:
        time_clean = _normalize_time_value(clock_time)
    except ValueError as exc:
        return False, str(exc), {}

    ok, clean_or_msg = _validate_timezone_name(timezone)
    if not ok:
        return False, clean_or_msg, {}
    tz_clean = clean_or_msg

    datetime_value = f"{date_clean} {time_clean}"
    if not DATETIME_RE.fullmatch(datetime_value):
        return False, "Tarih/saat birlestirilemedi", {}

    code, stdout, stderr = _run_host_script(
        HOST_TIMEZONE_SCRIPT, "set-clock", datetime_value, tz_clean
    )
    data = _parse_json(stdout)
    if not data.get("ok"):
        message = str(data.get("error") or stderr or "Sunucu saati ayarlanamadi")
        logger.warning("host set-clock basarisiz (%s): %s", code, message)
        return False, message, {}

    invalidate_timezone_cache()
    return True, "Sunucu tarihi, saati ve saat dilimi guncellendi", normalize_clock_payload(data)


def merge_clock_into_server_info(server_info: dict[str, Any]) -> dict[str, Any]:
    """Host saat bilgisini server_info uzerine yazar (settings fallback kullanmaz)."""
    clock = collect_host_clock()
    merged = dict(server_info)
    merged["host_clock"] = clock
    if clock.get("clock_ok"):
        for key in (
            "clock_epoch",
            "clock_datetime",
            "clock_date",
            "clock_time",
            "timezone",
            "timezone_label",
            "utc_offset",
            "ntp_synchronized",
            "rtc_in_local_tz",
        ):
            if clock.get(key) not in (None, ""):
                merged[key] = clock[key]
    return merged
