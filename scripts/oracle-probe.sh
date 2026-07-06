#!/bin/bash
# Oracle TRTEK directory ve sunucu bilgisi — SYSDBA (/ as sysdba) ile okunur.
# Kullanim: oracle-probe.sh <ORACLE_SID> [_password_ignored] [DIRECTORY_NAME]
set -euo pipefail

SID="${1:?ORACLE_SID gerekli}"
DIR_NAME="${3:-TRTEK}"

ORACLE_HOME="${ORACLE_HOME:-}"
if [[ -z "$ORACLE_HOME" && -f /etc/oratab ]]; then
  ORACLE_HOME="$(awk -F: '$0 !~ /^#/ && NF>=2 && $2 != "" { print $2; exit }' /etc/oratab)"
fi
if [[ -z "$ORACLE_HOME" && -d /u01/app/oracle/product ]]; then
  ORACLE_HOME="$(ls -d /u01/app/oracle/product/*/dbhome_1 /u01/app/oracle/product/*/db_1 /u01/app/oracle/product/*/db 2>/dev/null | head -1)"
fi

if [[ -z "$ORACLE_HOME" || ! -x "${ORACLE_HOME}/bin/sqlplus" ]]; then
  printf '%s\n' '{"ok":false,"error":"sqlplus bulunamadi"}'
  exit 1
fi

run_sqlplus() {
  local sql="$1"
  local sqlfile
  sqlfile="$(mktemp /tmp/oracle-probe.XXXXXX.sql)"
  chmod 644 "$sqlfile"
  {
    echo "whenever sqlerror exit sql.sqlcode"
    echo "conn / as sysdba"
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

translate_error() {
  local raw="$1"
  if echo "$raw" | grep -qi 'ORA-01034'; then
    echo "Oracle instance ayakta degil (SID=${SID}). srvctl veya sqlplus startup ile baslatin."
  elif echo "$raw" | grep -qi 'ORA-01031'; then
    echo "SYSDBA yetkisi yok (SID=${SID}). oracle OS kullanicisi ile calistirin."
  else
    echo "$raw"
  fi
}

ERROR=""
DIR_PATH=""
VERSION=""

SQL_OUT="$(run_sqlplus "SELECT TRIM(directory_path) FROM dba_directories WHERE UPPER(directory_name)=UPPER('${DIR_NAME}');" || true)"
if echo "$SQL_OUT" | grep -qiE 'ORA-|SP2-'; then
  ERROR="$(translate_error "$(echo "$SQL_OUT" | grep -oiE 'ORA-[0-9]+:.*|SP2-[0-9]+:.*' | head -1 | tr -d '\r')")"
  [[ -z "$ERROR" ]] && ERROR="Oracle baglanti hatasi (SID=${SID})"
else
  DIR_PATH="$(echo "$SQL_OUT" | tr -d '\r' | sed '/^$/d' | head -1 | xargs)"
  if [[ -z "$DIR_PATH" ]]; then
    ERROR="Oracle directory bulunamadi: ${DIR_NAME} (SID=${SID})"
  else
    VER_OUT="$(run_sqlplus "SELECT TRIM(version) FROM v\$instance;" || true)"
    if ! echo "$VER_OUT" | grep -qiE 'ORA-|SP2-'; then
      VERSION="$(echo "$VER_OUT" | tr -d '\r' | sed '/^$/d' | head -1 | xargs)"
    fi
  fi
fi

HOSTNAME="$(hostname -s 2>/dev/null || hostname)"
ORACLE_VER=""
if [[ -n "$VERSION" ]]; then
  ORACLE_VER="$(echo "$VERSION" | awk -F. '{print $1"."$2"."$3"."$4}')"
fi

if [[ -n "$DIR_PATH" ]]; then
  DIR_PATH="${DIR_PATH%/}/"
fi

export PROBE_OK ERROR SID DIR_NAME DIR_PATH VERSION ORACLE_VER HOSTNAME
python - <<'PY'
import json, os

ok = not bool(os.environ.get("ERROR"))
data = {
    "ok": ok,
    "error": os.environ.get("ERROR", ""),
    "oracle_sid": os.environ.get("SID", ""),
    "directory": os.environ.get("DIR_NAME", "TRTEK"),
    "directory_path": os.environ.get("DIR_PATH", ""),
    "directorydizini": os.environ.get("DIR_PATH", ""),
    "yedek_dir": os.environ.get("DIR_PATH", "").rstrip("/"),
    "oracle_ver": os.environ.get("ORACLE_VER", ""),
    "oracle_version_full": os.environ.get("VERSION", ""),
    "hostname": os.environ.get("HOSTNAME", ""),
    "used_sysdba": True,
}
print(json.dumps(data, ensure_ascii=False))
PY
