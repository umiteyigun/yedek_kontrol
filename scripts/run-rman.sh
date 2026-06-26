#!/bin/bash
set -euo pipefail

RAW_TIP="${1:-RMAN_FULL}"
YEDEK_DIR="/yedek/orayedek"
RUN_LOG="${YEDEK_DIR}/panel-rman-$(date +%Y%m%d%H%M%S).log"
STATUS_FILE="${YEDEK_DIR}/.backup-status.json"

TIP="$RAW_TIP"
INSTANCE_ID=""
if [[ "$RAW_TIP" == *:* ]]; then
  TIP="${RAW_TIP%%:*}"
  INSTANCE_ID="${RAW_TIP#*:}"
fi

if [[ "$TIP" != "RMAN_FULL" && "$TIP" != "RMAN_INCR" && "$TIP" != "RMAN_FULL_MANUAL" ]]; then
  echo "Gecersiz RMAN tipi: $TIP" >&2
  exit 1
fi

write_status() {
  local state="$1"
  local exit_code="${2:-0}"
  cat >"$STATUS_FILE" <<EOF
{"state":"$state","tip":"$TIP","instance_id":"$INSTANCE_ID","exit_code":$exit_code,"log_file":"$(basename "$RUN_LOG")","backup_kind":"rman","updated_at":"$(date -Iseconds)"}
EOF
}

write_status "running" 0

prepare_rman_dest() {
  local inst_id="$1"
  local cfg="/yedek/config/instances/${inst_id}.sh"
  [[ -n "$inst_id" && -f "$cfg" ]] || return 0
  local dest=""
  dest="$(grep -m1 '^rman_dest=' "$cfg" | cut -d= -f2- | tr -d "'\"")"
  [[ -n "$dest" ]] || return 0
  mkdir -p "$dest/full" "$dest/fark" "$dest/full/manuel"
  chown -R oracle:oinstall "$dest" 2>/dev/null \
    || chown -R oracle:dba "$dest" 2>/dev/null \
    || true
}

if [[ -n "$INSTANCE_ID" ]]; then
  prepare_rman_dest "$INSTANCE_ID"
fi

if ! DISK_MSG="$(/yedek/config/disk-check-backup.sh "$TIP" "$INSTANCE_ID" 2>&1)"; then
  write_status "skipped" 12
  cat >"$STATUS_FILE" <<EOF
{"state":"skipped","tip":"$TIP","instance_id":"$INSTANCE_ID","exit_code":12,"log_file":"$(basename "$RUN_LOG")","backup_kind":"rman","reason":"${DISK_MSG//$'\n'/ }","updated_at":"$(date -Iseconds)"}
EOF
  echo "=== RMAN atlandi (disk): $DISK_MSG ===" >>"$RUN_LOG"
  exit 12
fi
set +e
{
  echo "=== RMAN basladi: $(date) tip=$TIP instance=${INSTANCE_ID:-all} ==="
  if ! id oracle &>/dev/null; then
    echo "HATA: oracle kullanicisi bulunamadi"
    exit 10
  fi
  if [ ! -x /usr/bin/rman.sh ]; then
    echo "HATA: /usr/bin/rman.sh bulunamadi"
    exit 11
  fi
  if [[ -n "$INSTANCE_ID" ]]; then
    su - oracle -c "/usr/bin/rman.sh $TIP $INSTANCE_ID"
  else
    su - oracle -c "/usr/bin/rman.sh $TIP"
  fi
} >>"$RUN_LOG" 2>&1
EXIT_CODE=$?
set -e
recover_cold_oracle() {
  local inst_id="$1"
  local flag="/yedek/orayedek/.rman-cold-${inst_id}.flag"
  [[ -n "$inst_id" && -f "$flag" ]] || return 0
  local sid="orcl"
  local cfg="/yedek/config/instances/${inst_id}.sh"
  if [[ -f "$cfg" ]]; then
    sid="$(grep -m1 '^ORACLE_SID=' "$cfg" | cut -d= -f2- | tr -d "'\"")"
  fi
  echo "=== RMAN cold backup guvenlik: DB aciliyor instance=${inst_id} sid=${sid} ===" >>"$RUN_LOG"
  if id oracle &>/dev/null; then
    su - oracle -c "export ORACLE_SID='${sid}'; sqlplus -s / as sysdba <<'SQL'
whenever sqlerror continue
startup;
alter database open;
exit;
SQL" >>"$RUN_LOG" 2>&1 || true
  fi
  rm -f "$flag"
}

if [[ -n "$INSTANCE_ID" ]]; then
  recover_cold_oracle "$INSTANCE_ID"
fi

if [ "$EXIT_CODE" -eq 0 ]; then
  write_status "done" "$EXIT_CODE"
else
  write_status "failed" "$EXIT_CODE"
fi
echo "=== RMAN bitti: $(date) exit=$EXIT_CODE ===" >>"$RUN_LOG"
exit "$EXIT_CODE"
