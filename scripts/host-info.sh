#!/bin/bash
# Host isletim sistemi, CPU, RAM ve disk ozeti — JSON cikti
set -euo pipefail

HOSTNAME="$(hostname -s 2>/dev/null || hostname)"
OS_NAME="Linux"
if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  OS_NAME="${PRETTY_NAME:-${NAME:-Linux}}"
fi
KERNEL="$(uname -r)"
ARCH="$(uname -m)"
CPU_CORES="$(nproc 2>/dev/null || echo 1)"
CPU_MODEL="$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | xargs || true)"
LOAD_AVG="$(awk '{print $1", "$2", "$3}' /proc/loadavg 2>/dev/null || echo "-")"

MEM_TOTAL_MB="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)"
if grep -q '^MemAvailable:' /proc/meminfo 2>/dev/null; then
  MEM_AVAIL_MB="$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)"
else
  MEM_AVAIL_MB="$(
    awk '
      $1 == "MemFree:" {f=$2}
      $1 == "Buffers:" {b=$2}
      $1 == "Cached:" {c=$2}
      END {print int((f+b+c)/1024)}
    ' /proc/meminfo
  )"
fi
MEM_USED_MB=$((MEM_TOTAL_MB - MEM_AVAIL_MB))
if [[ "$MEM_TOTAL_MB" -gt 0 ]]; then
  MEM_USED_PCT=$((MEM_USED_MB * 100 / MEM_TOTAL_MB))
else
  MEM_USED_PCT=0
fi

disk_field() {
  local mount="$1"
  local line
  line="$(df -P -BG "$mount" 2>/dev/null | tail -1 || true)"
  if [[ -z "$line" ]]; then
    echo "0 0 0"
    return
  fi
  echo "$line" | awk '{
    gsub(/G/,"",$2); gsub(/G/,"",$3);
    pct=($2>0)?int($3*100/$2):0;
    print $2, $3, pct
  }'
}

read -r DISK_ROOT_TOTAL DISK_ROOT_USED DISK_ROOT_PCT <<<"$(disk_field /)"
read -r DISK_YEDEK_TOTAL DISK_YEDEK_USED DISK_YEDEK_PCT <<<"$(disk_field /yedek)"

CLOCK_EPOCH="$(date +%s)"
CLOCK_DATETIME="$(date '+%d.%m.%Y %H:%M:%S')"
CLOCK_DATE="$(date '+%d.%m.%Y')"
CLOCK_TIME="$(date '+%H:%M:%S')"
if command -v timedatectl >/dev/null 2>&1; then
  TIMEZONE="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
else
  TIMEZONE=""
fi
if [[ -z "$TIMEZONE" ]] && [[ -L /etc/localtime ]]; then
  TIMEZONE="$(readlink -f /etc/localtime | sed 's|.*/zoneinfo/||')"
fi
TIMEZONE="${TIMEZONE:-UTC}"
UTC_OFFSET="$(date +%z | sed 's/\([+-][0-9][0-9]\)\([0-9][0-9]\)/\1:\2/')"

export HOSTNAME OS_NAME KERNEL ARCH CPU_CORES CPU_MODEL LOAD_AVG
export MEM_TOTAL_MB MEM_USED_MB MEM_AVAIL_MB MEM_USED_PCT
export DISK_ROOT_TOTAL DISK_ROOT_USED DISK_ROOT_PCT
export DISK_YEDEK_TOTAL DISK_YEDEK_USED DISK_YEDEK_PCT
export CLOCK_EPOCH CLOCK_DATETIME CLOCK_DATE CLOCK_TIME TIMEZONE UTC_OFFSET

python - <<'PY'
import json, os

def num(name, default=0):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default

data = {
    "ok": True,
    "hostname": os.environ.get("HOSTNAME", ""),
    "os_name": os.environ.get("OS_NAME", ""),
    "kernel": os.environ.get("KERNEL", ""),
    "arch": os.environ.get("ARCH", ""),
    "cpu_model": os.environ.get("CPU_MODEL", ""),
    "cpu_cores": int(num("CPU_CORES", 1)),
    "load_avg": os.environ.get("LOAD_AVG", ""),
    "mem_total_mb": int(num("MEM_TOTAL_MB")),
    "mem_used_mb": int(num("MEM_USED_MB")),
    "mem_avail_mb": int(num("MEM_AVAIL_MB")),
    "mem_used_pct": int(num("MEM_USED_PCT")),
    "disk_root_total_gb": num("DISK_ROOT_TOTAL"),
    "disk_root_used_gb": num("DISK_ROOT_USED"),
    "disk_root_pct": int(num("DISK_ROOT_PCT")),
    "disk_yedek_total_gb": num("DISK_YEDEK_TOTAL"),
    "disk_yedek_used_gb": num("DISK_YEDEK_USED"),
    "disk_yedek_pct": int(num("DISK_YEDEK_PCT")),
    "clock_epoch": int(num("CLOCK_EPOCH")),
    "clock_datetime": os.environ.get("CLOCK_DATETIME", ""),
    "clock_date": os.environ.get("CLOCK_DATE", ""),
    "clock_time": os.environ.get("CLOCK_TIME", ""),
    "timezone": os.environ.get("TIMEZONE", "UTC"),
    "utc_offset": os.environ.get("UTC_OFFSET", ""),
}
print(json.dumps(data, ensure_ascii=False))
PY
