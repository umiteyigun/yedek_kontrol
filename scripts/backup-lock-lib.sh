#!/bin/bash
# Yedek kilidi / hayalet surec yardimcilari — backup-watcher + run-backup ortak.
# shellcheck disable=SC2034
set -euo pipefail

: "${LOCK_DEFAULT:=/yedek/orayedek/.backup-running}"
: "${LOCK_ORPHAN_SEC:=3600}"
: "${FTP_STALE_SEC:=18000}"
: "${NOTIFY_STALE_SEC:=900}"

# tip: GUNLUK:instance:ftp_target veya RMAN_*:instance
backup_parse_tip() {
  local raw="${1:-}"
  BACKUP_TIP="${raw}"
  BACKUP_INSTANCE_ID=""
  BACKUP_FTP_TARGET="primary"
  if [[ "$raw" == *:* ]]; then
    BACKUP_TIP="${raw%%:*}"
    local rest="${raw#*:}"
    if [[ "$rest" == *:* ]]; then
      BACKUP_INSTANCE_ID="${rest%%:*}"
      BACKUP_FTP_TARGET="${rest#*:}"
    else
      BACKUP_INSTANCE_ID="$rest"
    fi
  fi
}

backup_instance_dir() {
  local inst_id="${1:-}"
  local dir="/yedek/orayedek"
  if [[ -n "$inst_id" && -f "/yedek/config/instances/${inst_id}.sh" ]]; then
    local raw
    raw="$(grep -m1 '^directorydizini=' "/yedek/config/instances/${inst_id}.sh" | cut -d= -f2- | tr -d "'\"")"
    raw="${raw%/}"
    [[ -n "$raw" ]] && dir="$raw"
  fi
  printf '%s\n' "$dir"
}

backup_lock_for_tip() {
  local tip="${1:-}"
  backup_parse_tip "$tip"
  local dir
  dir="$(backup_instance_dir "$BACKUP_INSTANCE_ID")"
  printf '%s\n' "${dir%/}/.backup-running"
}

backup_lock_paths_for_tip() {
  local tip="${1:-}"
  local inst_lock
  inst_lock="$(backup_lock_for_tip "$tip")"
  printf '%s\n' "$inst_lock"
  if [[ "$inst_lock" != "$LOCK_DEFAULT" ]]; then
    printf '%s\n' "$LOCK_DEFAULT"
  fi
}

backup_lock_age_sec() {
  local f="$1"
  [[ -f "$f" ]] || return 1
  local now mtime
  now="$(date +%s)"
  mtime="$(stat -c %Y "$f" 2>/dev/null || echo 0)"
  echo $((now - mtime))
}

backup_remove_locks_for_tip() {
  local tip="${1:-}"
  local lf
  while IFS= read -r lf; do
    [[ -n "$lf" ]] && rm -f "$lf" 2>/dev/null || true
  done < <(backup_lock_paths_for_tip "$tip")
}

backup_remove_all_known_locks() {
  local lf
  while IFS= read -r lf; do
    [[ -f "$lf" ]] || continue
    rm -f "$lf" 2>/dev/null || true
  done < <(
    {
      printf '%s\n' "$LOCK_DEFAULT"
      find /yedek /u01/app/oracle -name '.backup-running' 2>/dev/null || true
    } | sort -u
  )
}

# Gercek yedek isi: expdp, run-backup, ftp-put, gzip(.dmp), oracle altinda yedek zinciri
backup_work_active() {
  if pgrep -f 'run-backup\.sh' >/dev/null 2>&1; then
    return 0
  fi
  if pgrep -f 'ftp-put\.py' >/dev/null 2>&1; then
    return 0
  fi
  if pgrep -f 'expdp |/exp ' >/dev/null 2>&1; then
    return 0
  fi
  if pgrep -f 'gzip.*\.dmp' >/dev/null 2>&1; then
    return 0
  fi
  if pgrep -f 'run-rman\.sh|/usr/bin/rman\.sh' >/dev/null 2>&1; then
    return 0
  fi
  # yedek.sh + aktif alt is (expdp/gzip/ftp)
  if pgrep -f '/usr/bin/yedek\.sh' >/dev/null 2>&1; then
    if pgrep -f 'expdp |gzip |ftp-put\.py' >/dev/null 2>&1; then
      return 0
    fi
    # oracle altinda calisan yedek.sh (su - oracle)
    local pid
    while read -r pid; do
      [[ -n "$pid" ]] || continue
      if pstree -p "$pid" 2>/dev/null | grep -qE 'expdp|gzip|ftp-put'; then
        return 0
      fi
    done < <(pgrep -f '/usr/bin/yedek\.sh' 2>/dev/null || true)
  fi
  return 1
}

backup_kill_orphan_yedek_procs() {
  if backup_work_active; then
    return 0
  fi
  pkill -f '/usr/bin/yedek\.sh' 2>/dev/null || true
  pkill -f 'run-backup\.sh' 2>/dev/null || true
  sleep 1
  pkill -9 -f '/usr/bin/yedek\.sh' 2>/dev/null || true
  pkill -9 -f 'run-backup\.sh' 2>/dev/null || true
}

backup_local_artifact_complete() {
  local sf="${1:-}"
  local py
  if command -v python3 >/dev/null 2>&1; then
    py=python3
  elif command -v python >/dev/null 2>&1; then
    py=python
  else
    return 1
  fi
  [[ -f "$sf" ]] || return 1
  "$py" - "$sf" <<'PY'
from __future__ import print_function
import json
import os
import sys

path = sys.argv[1]
try:
    d = json.load(open(path))
except Exception:
    sys.exit(1)
inst = (d.get("instance_id") or "").strip()
tip = (d.get("tip") or "GUNLUK").strip()
log_name = (d.get("log_file") or "").strip()
base_dir = "/yedek/orayedek"
if inst:
    inst_path = os.path.join("/yedek/config/instances", inst + ".sh")
    if os.path.isfile(inst_path):
        for line in open(inst_path):
            line = line.strip()
            if line.startswith("directorydizini="):
                raw = line.split("=", 1)[1].strip().strip("'\"")
                if raw:
                    base_dir = raw.rstrip("/")
                break
if log_name:
    log_path = os.path.join(base_dir, log_name)
    if os.path.isfile(log_path):
        for line in open(log_path):
            if "Yedek tamamlandi" in line and "exit=0" in line:
                sys.exit(0)
            if "=== Yedek bitti:" in line and "exit=0" in line:
                sys.exit(0)
# Son .dmp.gz bugun ve >100MB
import glob
from datetime import datetime
today = datetime.now().strftime("%Y%m%d")
for pattern in ("*.dmp.gz", "*.dmp"):
    for fp in sorted(glob.glob(os.path.join(base_dir, pattern)), key=os.path.getmtime, reverse=True):
        name = os.path.basename(fp)
        if today in name and os.path.getsize(fp) > 100 * 1024 * 1024:
            sys.exit(0)
sys.exit(1)
PY
}
