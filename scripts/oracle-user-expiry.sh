#!/bin/bash
# Oracle kullanici sifre bitis sureleri — SYSDBA ile okunur.
# Kullanim: oracle-user-expiry.sh <ORACLE_SID>
set -euo pipefail

SID="${1:?ORACLE_SID gerekli}"

ORACLE_HOME="${ORACLE_HOME:-}"
if [[ -z "$ORACLE_HOME" && -f /etc/oratab ]]; then
  ORACLE_HOME="$(awk -F: '$0 !~ /^#/ && NF>=2 && $2 != "" { print $2; exit }' /etc/oratab)"
fi
if [[ -z "$ORACLE_HOME" && -d /u01/app/oracle/product ]]; then
  ORACLE_HOME="$(ls -d /u01/app/oracle/product/*/dbhome_1 /u01/app/oracle/product/*/db_1 /u01/app/oracle/product/*/db 2>/dev/null | head -1)"
fi

if [[ -z "$ORACLE_HOME" || ! -x "${ORACLE_HOME}/bin/sqlplus" ]]; then
  printf '%s\n' '{"ok":false,"error":"sqlplus bulunamadi","users":[]}'
  exit 1
fi

run_sqlplus() {
  local sql="$1"
  local sqlfile
  sqlfile="$(mktemp /tmp/oracle-user-expiry.XXXXXX.sql)"
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

# CSV: username|account_status|expiry_date|days_left
EXPIRY_SQL="SELECT TRIM(username)||'|'||TRIM(account_status)||'|'||
NVL(TO_CHAR(expiry_date,'YYYY-MM-DD'),'')||'|'||
NVL(TO_CHAR(ROUND(expiry_date - SYSDATE)),'')
FROM dba_users
WHERE username IN (
  'AKILU','DBSNMP','PACSDB','SYS','SYSMAN','SYSTEM','TRTEKLOG'
)
ORDER BY expiry_date NULLS LAST, username;"

ERROR=""
CSV_LINES=""

SQL_OUT="$(run_sqlplus "$EXPIRY_SQL" || true)"
if echo "$SQL_OUT" | grep -qiE 'ORA-|SP2-'; then
  ERROR="$(translate_error "$(echo "$SQL_OUT" | grep -oiE 'ORA-[0-9]+:.*|SP2-[0-9]+:.*' | head -1 | tr -d '\r')")"
  [[ -z "$ERROR" ]] && ERROR="Oracle sorgu hatasi (SID=${SID})"
else
  CSV_LINES="$(echo "$SQL_OUT" | tr -d '\r' | sed '/^$/d')"
fi

export ERROR SID CSV_LINES
python - <<'PY'
import json
import os

error = os.environ.get("ERROR", "")
lines = os.environ.get("CSV_LINES", "")
users = []
for line in lines.splitlines():
    line = line.strip()
    if not line or "|" not in line:
        continue
    parts = line.split("|")
    if len(parts) < 3:
        continue
    username = parts[0].strip()
    status = parts[1].strip()
    expiry = parts[2].strip() or None
    days_left = None
    if len(parts) >= 4 and parts[3].strip():
        try:
            days_left = int(float(parts[3].strip()))
        except ValueError:
            days_left = None
    if not username:
        continue
    users.append(
        {
            "username": username,
            "account_status": status,
            "expiry_date": expiry,
            "days_left": days_left,
        }
    )

ok = not bool(error)
data = {
    "ok": ok,
    "error": error,
    "oracle_sid": os.environ.get("SID", ""),
    "users": users,
}
print(json.dumps(data, ensure_ascii=False))
PY
