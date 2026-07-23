#!/bin/bash
set -euo pipefail

RAW_TIP="${1:-GUNLUK}"
YEDEK_DIR="/yedek/orayedek"
RUN_LOG=""
STATUS_FILE=""
BACKUP_STATUS_FILE=""
FTP_STALE_SEC="${FTP_STALE_SEC:-10800}"
NOTIFY_STALE_SEC="${NOTIFY_STALE_SEC:-900}"

# shellcheck source=/dev/null
source /yedek/config/backup-status-lib.sh

# Defensive: if a previous FTP hang left status running past FTP_STALE_SEC,
# clear it before starting a new job (watcher also reclaims the lock).
_clear_stale_ftp_status() {
  local sf="${YEDEK_DIR}/.backup-status.json"
  local py=""
  if command -v python3 >/dev/null 2>&1; then
    py=python3
  elif command -v python >/dev/null 2>&1; then
    py=python
  else
    return 0
  fi
  [[ -f "$sf" ]] || return 0
  "$py" - "$sf" "$FTP_STALE_SEC" <<'PY' || true
from __future__ import print_function
import json, sys, os
from datetime import datetime
path, lim = sys.argv[1], int(sys.argv[2])
try:
    d = json.load(open(path))
except Exception:
    sys.exit(0)
if d.get("state") != "running" or d.get("stage") != "ftp_upload":
    sys.exit(0)
raw = str(d.get("stage_started_at") or d.get("started_at") or "")[:19]
try:
    started = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S")
except Exception:
    sys.exit(0)
age = int((datetime.now() - started).total_seconds())
if age < lim:
    sys.exit(0)
d["state"] = "failed"
d["stage"] = "failed"
d["exit_code"] = 1
d["reason"] = "stale FTP status cleared by run-backup (age=%ss)" % age
d["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(d, fh)
os.rename(tmp, path)
print("cleared stale ftp status age=%s" % age)
PY
  rm -f /yedek/config/ftp-upload.state 2>/dev/null || true
}

_clear_stale_notify_status() {
  local sf="${YEDEK_DIR}/.backup-status.json"
  local py=""
  if command -v python3 >/dev/null 2>&1; then
    py=python3
  elif command -v python >/dev/null 2>&1; then
    py=python
  else
    return 0
  fi
  [[ -f "$sf" ]] || return 0
  "$py" - "$sf" "$NOTIFY_STALE_SEC" <<'PY' || true
from __future__ import print_function
import json, sys, os
from datetime import datetime
path, lim = sys.argv[1], int(sys.argv[2])
try:
    d = json.load(open(path))
except Exception:
    sys.exit(0)
if d.get("state") != "running" or d.get("stage") != "notifying":
    sys.exit(0)
raw = str(d.get("stage_started_at") or d.get("started_at") or "")[:19]
try:
    started = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S")
except Exception:
    sys.exit(0)
age = int((datetime.now() - started).total_seconds())
if age < lim:
    sys.exit(0)
d["state"] = "done"
d["stage"] = "done"
d["exit_code"] = 0
d["reason"] = "stale notify status cleared by run-backup (age=%ss)" % age
d["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(d, fh)
os.rename(tmp, path)
print("cleared stale notify status age=%s" % age)
PY
}

_clear_stale_ftp_status
_clear_stale_notify_status


resolve_instance_backup_dir() {
  local inst_id="${1:-}"
  local cfg="" dest=""
  if [[ -n "$inst_id" && -f "/yedek/config/instances/${inst_id}.sh" ]]; then
    dest="$(grep -m1 '^directorydizini=' "/yedek/config/instances/${inst_id}.sh" | cut -d= -f2- | tr -d "'\"")"
    dest="${dest%/}"
    [[ -n "$dest" ]] && printf '%s\n' "$dest" && return 0
  fi
  printf '%s\n' "$YEDEK_DIR"
}

TIP="$RAW_TIP"
INSTANCE_ID=""
FTP_TARGET="primary"
if [[ "$RAW_TIP" == *:* ]]; then
  TIP="${RAW_TIP%%:*}"
  rest="${RAW_TIP#*:}"
  if [[ "$rest" == *:* ]]; then
    INSTANCE_ID="${rest%%:*}"
    FTP_TARGET="${rest#*:}"
  else
    INSTANCE_ID="$rest"
  fi
fi

if [[ "$TIP" != "GUNLUK" && "$TIP" != "HAFTALIK" ]]; then
  echo "Gecersiz tip: $TIP" >&2
  exit 1
fi

write_status() {
  local state="$1"
  local exit_code="${2:-0}"
  if [[ "$state" == "running" ]]; then
    bs_init \
      --state running \
      --stage preflight \
      --tip "$TIP" \
      --instance-id "$INSTANCE_ID" \
      --log-file "$(basename "$RUN_LOG")" \
      --backup-kind expdp
    bs_ensure_writable
    return
  fi
  bs_finish --state "$state" --exit-code "$exit_code"
}

_oracle_chown() {
  chown -R oracle:oinstall "$@" 2>/dev/null \
    || chown -R oracle:dba "$@" 2>/dev/null \
    || true
}

prepare_backup_dirs() {
  local inst_id="${1:-}"
  local cfg="" dest="" iid=""

  mkdir -p "$YEDEK_DIR"
  _oracle_chown "$YEDEK_DIR"
  chmod 775 "$YEDEK_DIR" 2>/dev/null || true

  mkdir -p /yedek/config
  touch /yedek/config/ftp-upload.log 2>/dev/null || true
  _oracle_chown /yedek/config/ftp-upload.log

  if [[ -n "$inst_id" ]]; then
    cfg="/yedek/config/instances/${inst_id}.sh"
    if [[ -f "$cfg" ]]; then
      dest="$(grep -m1 '^directorydizini=' "$cfg" | cut -d= -f2- | tr -d "'\"")"
      if [[ -n "$dest" ]]; then
        mkdir -p "$dest"
        _oracle_chown "$dest"
        chmod 775 "$dest" 2>/dev/null || true
      fi
    fi
    return 0
  fi

  if [[ -f /yedek/config/instances.list ]]; then
    while IFS= read -r iid || [[ -n "$iid" ]]; do
      iid="${iid//[[:space:]]/}"
      [[ -n "$iid" ]] || continue
      prepare_backup_dirs "$iid"
    done < /yedek/config/instances.list
  fi
}

prepare_backup_dirs "$INSTANCE_ID"
INSTANCE_DIR="$(resolve_instance_backup_dir "$INSTANCE_ID")"
RUN_LOG="${INSTANCE_DIR}/panel-backup-$(date +%Y%m%d%H%M%S).log"
STATUS_FILE="${INSTANCE_DIR}/.backup-status.json"
BACKUP_STATUS_FILE="$STATUS_FILE"
write_status "running" 0
if ! DISK_MSG="$(/yedek/config/disk-check-backup.sh "$TIP" "$INSTANCE_ID" 2>&1)"; then
  bs_finish --state skipped --exit-code 12
  bs_set_reason "$DISK_MSG" || true
  echo "=== Yedek atlandi (disk): $DISK_MSG ===" >>"$RUN_LOG"
  exit 12
fi
{
  echo "=== Yedek basladi: $(date) tip=$TIP instance=${INSTANCE_ID:-all} ==="
  if ! id oracle &>/dev/null; then
    echo "HATA: oracle kullanicisi bulunamadi"
    exit 10
  fi
  if [ ! -x /usr/bin/yedek.sh ]; then
    echo "HATA: /usr/bin/yedek.sh bulunamadi"
    exit 11
  fi
  if [[ -n "$INSTANCE_ID" ]]; then
    su - oracle -c "FTP_TARGET=${FTP_TARGET} /usr/bin/yedek.sh $TIP $INSTANCE_ID"
  else
    su - oracle -c "FTP_TARGET=${FTP_TARGET:-primary} /usr/bin/yedek.sh $TIP"
  fi
} >>"$RUN_LOG" 2>&1

EXIT_CODE=$?
if [ "$EXIT_CODE" -eq 0 ]; then
  write_status "done" "$EXIT_CODE"
else
  write_status "failed" "$EXIT_CODE"
fi
echo "=== Yedek bitti: $(date) exit=$EXIT_CODE ===" >>"$RUN_LOG"
exit "$EXIT_CODE"
