#!/bin/bash
# Oracle kullanici (schema) listesi — SYSDBA ile okunur.
# Kullanim: oracle-schemas.sh <ORACLE_SID>
set -euo pipefail

SID="${1:?ORACLE_SID gerekli}"

ORACLE_HOME="${ORACLE_HOME:-}"
if [[ -z "$ORACLE_HOME" && -d /u01/app/oracle/product ]]; then
  ORACLE_HOME="$(ls -d /u01/app/oracle/product/*/db_1 2>/dev/null | head -1)"
fi

if [[ -z "$ORACLE_HOME" || ! -x "${ORACLE_HOME}/bin/sqlplus" ]]; then
  printf '%s\n' '{"ok":false,"error":"sqlplus bulunamadi","schemas":[]}'
  exit 1
fi

run_sqlplus() {
  local sql="$1"
  local sqlfile
  sqlfile="$(mktemp /tmp/oracle-schemas.XXXXXX.sql)"
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

translate_error() {
  local raw="$1"
  if echo "$raw" | grep -qi 'ORA-01034'; then
    echo "Oracle instance ayakta degil (SID=${SID})."
  elif echo "$raw" | grep -qi 'ORA-01031'; then
    echo "SYSDBA yetkisi yok (SID=${SID})."
  else
    echo "$raw"
  fi
}

SCHEMA_SQL="SELECT TRIM(username) FROM dba_users
WHERE account_status = 'OPEN'
AND username NOT IN (
  'ANONYMOUS','APEX_PUBLIC_USER','APPQOSSYS','CTXSYS','DBSNMP','DIP',
  'EXFSYS','FLOWS_FILES','MDSYS','MDDATA','MGMT_VIEW','OLAPSYS',
  'ORACLE_OCM','ORDDATA','ORDPLUGINS','ORDSYS','OUTLN','OWBSYS',
  'SI_INFORMTN_SCHEMA','SYS','SYSMAN','WMSYS','XDB'
)
AND username NOT LIKE 'APEX\_%' ESCAPE '\'
ORDER BY username;"

ERROR=""
SCHEMA_LINES=""

SQL_OUT="$(run_sqlplus "$SCHEMA_SQL" || true)"
if echo "$SQL_OUT" | grep -qiE 'ORA-|SP2-'; then
  ERROR="$(translate_error "$(echo "$SQL_OUT" | grep -oiE 'ORA-[0-9]+:.*|SP2-[0-9]+:.*' | head -1 | tr -d '\r')")"
  [[ -z "$ERROR" ]] && ERROR="Oracle sorgu hatasi (SID=${SID})"
else
  SCHEMA_LINES="$(echo "$SQL_OUT" | tr -d '\r' | sed '/^$/d')"
fi

export SCHEMA_OK ERROR SID SCHEMA_LINES
python - <<'PY'
import json, os

error = os.environ.get("ERROR", "")
lines = os.environ.get("SCHEMA_LINES", "")
schemas = [line.strip() for line in lines.splitlines() if line.strip()]
ok = not bool(error) and bool(schemas or not error)
if error:
    ok = False
data = {
    "ok": ok,
    "error": error,
    "oracle_sid": os.environ.get("SID", ""),
    "schemas": schemas,
}
print(json.dumps(data, ensure_ascii=False))
PY
