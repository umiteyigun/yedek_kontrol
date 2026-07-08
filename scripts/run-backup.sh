#!/bin/bash
set -euo pipefail

RAW_TIP="${1:-GUNLUK}"
YEDEK_DIR="/yedek/orayedek"
RUN_LOG="${YEDEK_DIR}/panel-backup-$(date +%Y%m%d%H%M%S).log"
STATUS_FILE="${YEDEK_DIR}/.backup-status.json"
BACKUP_STATUS_FILE="$STATUS_FILE"

# shellcheck source=/dev/null
source /yedek/config/backup-status-lib.sh

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

write_status "running" 0
if ! DISK_MSG="$(/yedek/config/disk-check-backup.sh "$TIP" "$INSTANCE_ID" 2>&1)"; then
  bs_finish --state skipped --exit-code 12
  python3 - "$STATUS_FILE" "$DISK_MSG" <<'PY'
import json, os, sys
from datetime import datetime
path, reason = sys.argv[1], sys.argv[2].replace("\n", " ")
data = {}
if os.path.isfile(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        pass
data["reason"] = reason
data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False)
PY
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
