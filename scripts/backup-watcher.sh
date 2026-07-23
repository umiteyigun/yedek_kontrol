#!/bin/bash
set -euo pipefail

TRIGGER="/yedek/config/backup.trigger"
LOCK="/yedek/orayedek/.backup-running"
WATCH_LOG="/yedek/orayedek/backup-watcher.log"
FTP_STALE_SEC="${FTP_STALE_SEC:-10800}"
FTP_STATE="/yedek/config/ftp-upload.state"
FTP_HELPER="/yedek/config/ftp-put.py"
STATUS_DEFAULT="/yedek/orayedek/.backup-status.json"

_wlog() {
  echo "$(date -Iseconds) $*" >>"$WATCH_LOG" 2>/dev/null || true
}

_ftp_py() {
  if command -v python3 >/dev/null 2>&1; then
    echo python3
  elif command -v python >/dev/null 2>&1; then
    echo python
  else
    return 1
  fi
}

_resolve_status_file() {
  local sf="$STATUS_DEFAULT"
  local py iid dir
  py="$(_ftp_py)" || { printf '%s\n' "$sf"; return 0; }
  [[ -f "$STATUS_DEFAULT" ]] || { printf '%s\n' "$sf"; return 0; }
  iid="$("$py" -c "
import json
try:
 d=json.load(open('$STATUS_DEFAULT'))
 print(d.get('instance_id') or '')
except Exception:
 print('')
" 2>/dev/null || true)"
  if [[ -n "${iid:-}" && -f "/yedek/config/instances/${iid}.sh" ]]; then
    dir="$(grep -m1 '^directorydizini=' "/yedek/config/instances/${iid}.sh" | cut -d= -f2- | tr -d "'\"")"
    dir="${dir%/}"
    if [[ -n "$dir" && -f "${dir}/.backup-status.json" ]]; then
      sf="${dir}/.backup-status.json"
    fi
  fi
  printf '%s\n' "$sf"
}

_status_field() {
  local sf="$1" key="$2"
  local py
  py="$(_ftp_py)" || return 1
  "$py" - "$sf" "$key" <<'PY'
from __future__ import print_function
import json, sys
path, key = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(path))
except Exception:
    print("")
    sys.exit(0)
print(d.get(key) or "")
PY
}

_status_age_sec() {
  local sf="$1"
  local py
  py="$(_ftp_py)" || return 1
  "$py" - "$sf" <<'PY'
from __future__ import print_function
import json, sys
from datetime import datetime
path = sys.argv[1]
try:
    d = json.load(open(path))
except Exception:
    print(-1)
    sys.exit(0)
raw = d.get("stage_started_at") or d.get("started_at") or ""
raw = str(raw).strip().replace("Z", "")
# strip timezone offset for simple parse
if len(raw) > 19 and (raw[19] == "+" or raw[19] == "-"):
    raw = raw[:19]
try:
    started = datetime.strptime(raw[:19], "%Y-%m-%dT%H:%M:%S")
except Exception:
    print(-1)
    sys.exit(0)
print(int((datetime.now() - started).total_seconds()))
PY
}

_cred_for_server() {
  # Sets FTP_USER FTP_PASS FTP_DIR from yedekconfig matching server IP
  local server="$1"
  FTP_USER=""
  FTP_PASS=""
  FTP_DIR="/"
  [[ -f /yedek/config/yedekconfig.sh ]] || return 1
  # shellcheck source=/dev/null
  source /yedek/config/yedekconfig.sh
  if [[ "${localftpip:-}" == "$server" ]]; then
    FTP_USER="${localftpuser:-}"
    FTP_PASS="${localftppass:-}"
    FTP_DIR="${localftpdir:-/}"
    return 0
  fi
  if [[ "${localftpip2:-}" == "$server" ]]; then
    FTP_USER="${localftpuser2:-}"
    FTP_PASS="${localftppass2:-}"
    FTP_DIR="${localftpdir2:-/}"
    return 0
  fi
  return 1
}

_kill_ftp_procs() {
  # Best-effort: hanging classic ftp and ftp-put helpers
  pkill -f '/usr/bin/ftp |^ftp |[[:space:]]ftp -' 2>/dev/null || true
  pkill -f 'ftp-put\.py' 2>/dev/null || true
  sleep 1
  pkill -9 -f 'ftp-put\.py' 2>/dev/null || true
  pkill -9 -f '/usr/bin/ftp |^ftp ' 2>/dev/null || true
}

_finish_status() {
  local sf="$1" state="$2" reason="$3"
  if [[ -x /yedek/config/backup-status-lib.sh ]]; then
    BACKUP_STATUS_FILE="$sf" \
      /yedek/config/backup-status-lib.sh finish --state "$state" --exit-code "$([[ "$state" == done ]] && echo 0 || echo 1)" 2>/dev/null || true
    BACKUP_STATUS_FILE="$sf" \
      /yedek/config/backup-status-lib.sh reason "$reason" 2>/dev/null || true
  else
    local py
    py="$(_ftp_py)" || return 0
    "$py" - "$sf" "$state" "$reason" <<'PY'
from __future__ import print_function
import json, sys, os
from datetime import datetime
path, state, reason = sys.argv[1:4]
try:
    d = json.load(open(path))
except Exception:
    d = {}
d["state"] = state
d["exit_code"] = 0 if state == "done" else 1
d["reason"] = reason
d["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(d, fh)
os.rename(tmp, path)
PY
  fi
}

reclaim_stale_ftp_lock() {
  # Returns 0 if lock was reclaimed (caller may proceed), 1 if still busy.
  [[ -f "$LOCK" ]] || return 0

  local sf stage age state
  sf="$(_resolve_status_file)"
  state="$(_status_field "$sf" state 2>/dev/null || true)"
  stage="$(_status_field "$sf" stage 2>/dev/null || true)"
  age="$(_status_age_sec "$sf" 2>/dev/null || echo -1)"

  # Also treat very old lock mtime as stale even without status
  local lock_age=0
  if [[ -f "$LOCK" ]]; then
    lock_age=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
  fi

  local is_ftp_stage=0
  if [[ "${stage:-}" == "ftp_upload" ]]; then
    is_ftp_stage=1
  fi
  # Active ftp-put state implies FTP stage even if status stale
  if [[ -f "$FTP_STATE" ]]; then
    is_ftp_stage=1
  fi

  if [[ "$is_ftp_stage" != "1" ]]; then
    return 1
  fi

  local stale_age="$age"
  if [[ "$stale_age" -lt 0 ]] 2>/dev/null; then
    stale_age="$lock_age"
  fi
  # Use the larger of status age / lock age
  if [[ "$lock_age" -gt "$stale_age" ]]; then
    stale_age="$lock_age"
  fi

  if [[ "$stale_age" -lt "$FTP_STALE_SEC" ]]; then
    return 1
  fi

  _wlog "stale FTP reclaim: age=${stale_age}s threshold=${FTP_STALE_SEC}s stage=${stage} status=${sf}"

  local local_path="" remote_name="" server="" user="" remote_dir="/" local_size=0
  local py match=0
  py="$(_ftp_py)" || true

  if [[ -f "$FTP_STATE" && -n "$py" ]]; then
    eval "$("$py" - "$FTP_STATE" <<'PY'
from __future__ import print_function
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
def q(v):
    return "'" + str(v).replace("'", "'\"'\"'") + "'"
print("local_path=%s" % q(d.get("local_path", "")))
print("remote_name=%s" % q(d.get("remote_name", "")))
print("server=%s" % q(d.get("server", "")))
print("user=%s" % q(d.get("user", "")))
print("remote_dir=%s" % q(d.get("remote_dir", "/")))
print("local_size=%s" % int(d.get("local_size") or 0))
PY
)"
  fi

  if [[ -n "${local_path:-}" && -f "$local_path" && -n "${server:-}" && -f "$FTP_HELPER" && -n "$py" ]]; then
    if _cred_for_server "$server"; then
      set +e
      "$py" "$FTP_HELPER" \
        --host "$server" \
        --user "$FTP_USER" \
        --password "$FTP_PASS" \
        --local "$local_path" \
        --remote "$remote_name" \
        --remote-dir "${FTP_DIR:-$remote_dir}" \
        --verify-only \
        --log /yedek/config/ftp-upload.log
      local vrc=$?
      set -e
      [[ "$vrc" -eq 0 ]] && match=1
    fi
  fi

  _kill_ftp_procs
  # Also stop orphaned yedek.sh under the lock if still running FTP stage
  pkill -f '/usr/bin/yedek\.sh' 2>/dev/null || true

  if [[ "$match" -eq 1 ]]; then
    _finish_status "$sf" done "stale FTP reclaim: remote SIZE matched (Ftp=1)"
    _wlog "stale FTP reclaim: SIZE match -> done, lock cleared"
  else
    _finish_status "$sf" failed "stale FTP reclaim: timeout/incomplete (Ftp=0)"
    _wlog "stale FTP reclaim: no SIZE match -> failed, lock cleared"
  fi

  rm -f "$LOCK" "$FTP_STATE" 2>/dev/null || true
  return 0
}

while true; do
  if [ -f "$TRIGGER" ]; then
    TIP="$(tr -d '[:space:]' <"$TRIGGER")"
    rm -f "$TRIGGER"
    if [ -f "$LOCK" ]; then
      if reclaim_stale_ftp_lock; then
        _wlog "stale lock reclaimed; continuing tip=$TIP"
      else
        _wlog "atlandi: zaten calisiyor"
        sleep 2
        continue
      fi
    fi
    touch "$LOCK"
    _wlog "baslatiliyor tip=$TIP"
    RAW_TIP="$TIP"
    if [[ "$RAW_TIP" == *:* ]]; then
      RAW_TIP="${RAW_TIP%%:*}"
    fi
    if [[ "$RAW_TIP" == RMAN_* ]]; then
      RUNNER="/yedek/config/run-rman.sh"
    else
      RUNNER="/yedek/config/run-backup.sh"
    fi
    "$RUNNER" "$TIP" || true
    rm -f "$LOCK"
  fi
  sleep 2
done
