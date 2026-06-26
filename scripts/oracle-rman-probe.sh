#!/bin/bash
# Oracle RMAN on kosullari: archivelog modu, log_mode
# Kullanim: oracle-rman-probe.sh <ORACLE_SID>
set -euo pipefail

SID="${1:?ORACLE_SID gerekli}"

ORACLE_HOME="${ORACLE_HOME:-}"
if [[ -z "$ORACLE_HOME" && -d /u01/app/oracle/product ]]; then
  ORACLE_HOME="$(ls -d /u01/app/oracle/product/*/db_1 2>/dev/null | head -1)"
fi

if [[ -z "$ORACLE_HOME" || ! -x "${ORACLE_HOME}/bin/sqlplus" ]]; then
  printf '%s\n' '{"ok":false,"error":"sqlplus bulunamadi","archivelog":false,"log_mode":"UNKNOWN"}'
  exit 1
fi

run_sqlplus() {
  local sql="$1"
  local sqlfile
  sqlfile="$(mktemp /tmp/oracle-rman-probe.XXXXXX.sql)"
  chmod 644 "$sqlfile"
  {
    echo "whenever sqlerror exit sql.sqlcode"
    echo "conn / as sysdba"
    echo "set heading off feedback off pagesize 0 linesize 200 trimspool on verify off"
    echo "$sql"
    echo "exit;"
  } >"$sqlfile"
  su - oracle -c "
    export ORACLE_SID='${SID}'
    export ORACLE_HOME='${ORACLE_HOME}'
    export PATH=\$ORACLE_HOME/bin:\$PATH
    \$ORACLE_HOME/bin/sqlplus -s /nolog @${sqlfile}
  " 2>&1
  rm -f "$sqlfile"
}

ERROR=""
LOG_MODE=""
ARCHIVELOG="false"

SQL_OUT="$(run_sqlplus "SELECT TRIM(log_mode) FROM v\$database;" || true)"
if echo "$SQL_OUT" | grep -qiE 'ORA-|SP2-'; then
  ERROR="$(echo "$SQL_OUT" | grep -oiE 'ORA-[0-9]+:.*|SP2-[0-9]+:.*' | head -1 | tr -d '\r')"
  [[ -z "$ERROR" ]] && ERROR="Oracle sorgu hatasi (SID=${SID})"
else
  LOG_MODE="$(echo "$SQL_OUT" | tr -d '\r' | sed '/^$/d' | head -1 | xargs)"
  if echo "$LOG_MODE" | grep -qi '^ARCHIVELOG$'; then
    ARCHIVELOG="true"
  fi
fi

export PROBE_OK ERROR SID LOG_MODE ARCHIVELOG
python - <<'PY'
import json, os

error = os.environ.get("ERROR", "")
log_mode = os.environ.get("LOG_MODE", "UNKNOWN")
arch = os.environ.get("ARCHIVELOG", "false").lower() == "true"
data = {
    "ok": not bool(error),
    "error": error,
    "oracle_sid": os.environ.get("SID", ""),
    "log_mode": log_mode or "UNKNOWN",
    "archivelog": arch,
}
print(json.dumps(data, ensure_ascii=False))
PY
