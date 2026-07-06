#!/bin/bash
# Oracle instance metrikleri — data boyutu, SGA, PGA (SYSDBA)
# Kullanim: oracle-stats.sh <ORACLE_SID>
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
  printf '%s\n' '{"ok":false,"error":"sqlplus bulunamadi","oracle_sid":"'"$SID"'"}'
  exit 0
fi

run_sqlplus() {
  local connect_line="$1"
  local sql="$2"
  local sqlfile
  sqlfile="$(mktemp /tmp/oracle-stats.XXXXXX.sql)"
  chmod 644 "$sqlfile"
  {
    echo "whenever sqlerror exit sql.sqlcode"
    echo "$connect_line"
    echo "set heading off feedback off pagesize 0 linesize 500 trimspool on verify off"
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

STATS_SQL="
SELECT ROUND(SUM(bytes)/POWER(1024,3),2) FROM (
  SELECT bytes FROM dba_data_files
  UNION ALL
  SELECT bytes FROM dba_temp_files
);
SELECT ROUND(SUM(value)/1024/1024,0) FROM v\$sga;
SELECT ROUND(value/1024/1024,0) FROM v\$parameter WHERE name='pga_aggregate_target';
SELECT TRIM(version) FROM v\$instance;
"

ERROR=""
SQL_OUT="$(run_sqlplus "conn / as sysdba" "$STATS_SQL" || true)"

if echo "$SQL_OUT" | grep -qiE 'ORA-|SP2-'; then
  ERROR="$(echo "$SQL_OUT" | grep -oiE 'ORA-[0-9]+:.*|SP2-[0-9]+:.*' | head -1 | tr -d '\r')"
  [[ -z "$ERROR" ]] && ERROR="Oracle istatistik sorgusu basarisiz (SID=${SID})"
else
  mapfile -t LINES < <(echo "$SQL_OUT" | tr -d '\r' | sed '/^$/d')
  DATA_GB="${LINES[0]:-}"
  SGA_MB="${LINES[1]:-}"
  PGA_MB="${LINES[2]:-}"
  VERSION="${LINES[3]:-}"
fi

export PROBE_OK ERROR SID DATA_GB SGA_MB PGA_MB VERSION
python - <<'PY'
import json, os

ok = not bool(os.environ.get("ERROR"))
def num(name):
    raw = os.environ.get(name, "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None

data = {
    "ok": ok,
    "error": os.environ.get("ERROR", ""),
    "oracle_sid": os.environ.get("SID", ""),
    "data_size_gb": num("DATA_GB"),
    "sga_mb": num("SGA_MB"),
    "pga_mb": num("PGA_MB"),
    "oracle_version_full": os.environ.get("VERSION", ""),
    "used_sysdba": True,
}
print(json.dumps(data, ensure_ascii=False))
PY
