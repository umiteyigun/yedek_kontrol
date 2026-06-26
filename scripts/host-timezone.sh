#!/bin/bash
# Host saat / saat dilimi okuma ve ayarlama — JSON cikti
set -euo pipefail

ACTION="${1:-get}"
ARG2="${2:-}"
ARG3="${3:-}"

read_timezone() {
  local tz=""
  if command -v timedatectl >/dev/null 2>&1; then
    tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
  fi
  if [[ -z "$tz" ]] && [[ -L /etc/localtime ]]; then
    tz="$(readlink -f /etc/localtime | sed 's|.*/zoneinfo/||')"
  fi
  echo "${tz:-UTC}"
}

emit_clock_json() {
  local tz utc_offset
  tz="$(read_timezone)"
  utc_offset="$(date +%z | sed 's/\([+-][0-9][0-9]\)\([0-9][0-9]\)/\1:\2/')"
  export TZ_NAME="$tz" UTC_OFFSET="$utc_offset"
  python - <<'PY'
import json, os, subprocess, time
from datetime import datetime

tz = os.environ.get("TZ_NAME", "UTC")
utc_offset = os.environ.get("UTC_OFFSET", "")
now = datetime.now()
props = {}
timezone_label = tz
ntp_synced = ""
rtc_in_local = ""

try:
    out = subprocess.check_output(["timedatectl", "show"], stderr=subprocess.PIPE)
    for line in out.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key.strip()] = value.strip()
except Exception:
    pass

if props.get("Timezone"):
    tz = props["Timezone"]

try:
    status = subprocess.check_output(["timedatectl"], stderr=subprocess.STDOUT)
    for line in status.splitlines():
        line = line.strip()
        if "Time zone:" in line:
            timezone_label = line.split("Time zone:", 1)[1].strip()
except Exception:
    timezone_label = tz

ntp_synced = props.get("NTPSynchronized", "")
rtc_in_local = props.get("LocalRTC", "")

print(json.dumps({
    "ok": True,
    "timezone": tz,
    "timezone_label": timezone_label,
    "utc_offset": utc_offset,
    "ntp_synchronized": ntp_synced,
    "rtc_in_local_tz": rtc_in_local,
    "clock_epoch": int(time.time()),
    "clock_datetime": now.strftime("%d.%m.%Y %H:%M:%S"),
    "clock_date": now.strftime("%d.%m.%Y"),
    "clock_time": now.strftime("%H:%M:%S"),
}, ensure_ascii=False))
PY
}

list_timezones_json() {
  python - <<'PY'
import json, os, subprocess

zones = []
try:
    out = subprocess.check_output(["timedatectl", "list-timezones"], stderr=subprocess.PIPE)
    zones = [line.strip() for line in out.splitlines() if line.strip()]
except Exception:
    base = "/usr/share/zoneinfo"
    if os.path.isdir(base):
        for root, dirs, files in os.walk(base):
            for name in files:
                if name in ("UTC", "posixrules", "localtime", "Factory", "posix", "right"):
                    continue
                if "." in name:
                    continue
                path = os.path.join(root, name)
                if os.path.isfile(path) and not os.path.islink(path):
                    rel = os.path.relpath(path, base).replace(os.sep, "/")
                    zones.append(rel)
        zones = sorted(set(zones))

if not zones:
    zones = ["UTC"]

print(json.dumps({"ok": True, "timezones": zones, "count": len(zones)}, ensure_ascii=False))
PY
}

apply_timezone() {
  local tz_name="$1"
  if [[ -z "$tz_name" ]] || [[ ! "$tz_name" =~ ^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$ ]]; then
    echo '{"ok":false,"error":"Gecersiz timezone adi"}'
    return 1
  fi
  if [[ ! -f "/usr/share/zoneinfo/$tz_name" ]]; then
    echo '{"ok":false,"error":"Timezone dosyasi bulunamadi"}'
    return 1
  fi
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-timezone "$tz_name"
  else
    ln -sf "/usr/share/zoneinfo/$tz_name" /etc/localtime
  fi
}

apply_datetime() {
  local dt="$1"
  if [[ ! "$dt" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}\ [0-9]{2}:[0-9]{2}:[0-9]{2}$ ]]; then
    echo '{"ok":false,"error":"Gecersiz tarih/saat formati"}'
    return 1
  fi
  if command -v timedatectl >/dev/null 2>&1; then
    timedatectl set-ntp false 2>/dev/null || true
    timedatectl set-time "$dt"
  else
    date -s "$dt"
  fi
}

if [[ "$ACTION" == "get" ]]; then
  emit_clock_json
  exit 0
fi

if [[ "$ACTION" == "list-timezones" ]]; then
  list_timezones_json
  exit 0
fi

if [[ "$ACTION" == "set" || "$ACTION" == "set-tz" ]]; then
  if ! apply_timezone "$ARG2"; then
    exit 1
  fi
  emit_clock_json
  exit 0
fi

if [[ "$ACTION" == "set-datetime" ]]; then
  if ! apply_datetime "$ARG2"; then
    exit 1
  fi
  emit_clock_json
  exit 0
fi

if [[ "$ACTION" == "set-clock" ]]; then
  if [[ -n "$ARG3" ]]; then
    if ! apply_timezone "$ARG3"; then
      exit 1
    fi
  fi
  if ! apply_datetime "$ARG2"; then
    exit 1
  fi
  emit_clock_json
  exit 0
fi

echo '{"ok":false,"error":"Gecersiz islem"}'
exit 1
