#!/bin/bash
set -euo pipefail

RAW_TIP="${1:-GUNLUK}"
YEDEK_DIR="/yedek/orayedek"
RUN_LOG="${YEDEK_DIR}/panel-backup-$(date +%Y%m%d%H%M%S).log"
STATUS_FILE="${YEDEK_DIR}/.backup-status.json"

TIP="$RAW_TIP"
INSTANCE_ID=""
if [[ "$RAW_TIP" == *:* ]]; then
  TIP="${RAW_TIP%%:*}"
  INSTANCE_ID="${RAW_TIP#*:}"
fi

if [[ "$TIP" != "GUNLUK" && "$TIP" != "HAFTALIK" ]]; then
  echo "Gecersiz tip: $TIP" >&2
  exit 1
fi

write_status() {
  local state="$1"
  local exit_code="${2:-0}"
  cat >"$STATUS_FILE" <<EOF
{"state":"$state","tip":"$TIP","instance_id":"$INSTANCE_ID","exit_code":$exit_code,"log_file":"$(basename "$RUN_LOG")","updated_at":"$(date -Iseconds)"}
EOF
}

write_status "running" 0
if ! DISK_MSG="$(/yedek/config/disk-check-backup.sh "$TIP" "$INSTANCE_ID" 2>&1)"; then
  write_status "skipped" 12
  cat >"$STATUS_FILE" <<EOF
{"state":"skipped","tip":"$TIP","instance_id":"$INSTANCE_ID","exit_code":12,"log_file":"$(basename "$RUN_LOG")","reason":"${DISK_MSG//$'\n'/ }","updated_at":"$(date -Iseconds)"}
EOF
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
    su - oracle -c "/usr/bin/yedek.sh $TIP $INSTANCE_ID"
  else
    su - oracle -c "/usr/bin/yedek.sh $TIP"
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
