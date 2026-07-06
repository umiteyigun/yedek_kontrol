#!/bin/bash
# Oracle tablespace ozeti ve datafile listesi (SYSDBA) — JSON cikti
# Kullanim: oracle-tablespaces.sh list <ORACLE_SID>
#           oracle-tablespaces.sh datafiles <ORACLE_SID> <TABLESPACE>
#           oracle-tablespaces.sh add <ORACLE_SID> <TABLESPACE> <FILE_PATH> <SIZE_MB> <AUTO_YES_NO> <NEXT_MB> <MAX_MB|UNLIMITED>
set -euo pipefail

MODE="${1:?list|datafiles|add}"
SID="${2:?ORACLE_SID gerekli}"
TS_NAME="${3:-}"
FILE_PATH="${4:-}"
SIZE_MB="${5:-}"
AUTO_EXT="${6:-no}"
NEXT_MB="${7:-0}"
MAX_SIZE="${8:-UNLIMITED}"

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
  local sql="$1"
  local sqlfile
  sqlfile="$(mktemp /tmp/oracle-ts.XXXXXX.sql)"
  chmod 644 "$sqlfile"
  {
    echo "whenever sqlerror exit sql.sqlcode"
    echo "conn / as sysdba"
    echo "set heading off feedback off pagesize 0 linesize 4000 trimspool on verify off tab off"
    echo "set numwidth 20"
    echo "alter session set nls_numeric_characters='.,';"
    echo "set colsep '|'"
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

LIST_SQL="
SELECT
  t.tablespace_name,
  DECODE(t.contents,'PERMANENT','Permanent','TEMPORARY','Temporary','UNDO','Undo',t.contents),
  t.status,
  ROUND(NVL(a.bytes,0)/POWER(1024,3),2),
  ROUND(NVL(f.bytes,0)/POWER(1024,3),2),
  ROUND((NVL(a.bytes,0)-NVL(f.bytes,0))/POWER(1024,3),2),
  CASE WHEN NVL(a.bytes,0)=0 THEN 0 ELSE ROUND((NVL(a.bytes,0)-NVL(f.bytes,0))*100/NVL(a.bytes,0)) END,
  ROUND(NVL(a.maxbytes,0)/POWER(1024,3),2),
  CASE WHEN NVL(a.maxbytes,0)=0 THEN 0 ELSE ROUND((NVL(a.bytes,0)-NVL(f.bytes,0))*100/NVL(a.maxbytes,0)) END,
  t.block_size,
  DECODE(t.bigfile,'YES','Yes','No'),
  t.extent_management,
  NVL(t.allocation_type,'-'),
  t.segment_space_management,
  DECODE(t.logging,'LOGGING','Logging','NOLOGGING','Nologging',t.logging)
FROM dba_tablespaces t
LEFT JOIN (
  SELECT tablespace_name,
         SUM(bytes) bytes,
         SUM(DECODE(autoextensible,'YES',maxbytes,bytes)) maxbytes
  FROM dba_data_files
  GROUP BY tablespace_name
) a ON t.tablespace_name = a.tablespace_name
LEFT JOIN (
  SELECT tablespace_name, SUM(bytes) bytes FROM dba_free_space GROUP BY tablespace_name
) f ON t.tablespace_name = f.tablespace_name
ORDER BY 1;
"

DATAFILES_SQL="
SELECT
  df.file_name,
  df.file_id,
  CASE WHEN df.bytes=0 THEN 0 ELSE ROUND((df.bytes-NVL(fs.free_bytes,0))*100/df.bytes) END,
  ROUND(df.bytes/POWER(1024,3),2),
  ROUND((df.bytes-NVL(fs.free_bytes,0))/POWER(1024,3),2),
  ROUND(NVL(fs.free_bytes,0)/POWER(1024,3),2),
  df.blocks,
  df.autoextensible,
  ROUND(df.increment_by * ts.block_size / POWER(1024,2)),
  CASE WHEN df.maxbytes >= 34359738368*1024 THEN 'UNLIMITED' ELSE TO_CHAR(ROUND(df.maxbytes/POWER(1024,2))) END,
  df.status,
  0
FROM dba_data_files df
JOIN dba_tablespaces ts ON df.tablespace_name = ts.tablespace_name
LEFT JOIN (
  SELECT file_id, SUM(bytes) free_bytes FROM dba_free_space GROUP BY file_id
) fs ON df.file_id = fs.file_id
WHERE df.tablespace_name = UPPER('${TS_NAME}')
ORDER BY df.file_id;
"

ERROR=""
ROWS=()

if [[ "$MODE" == "list" ]]; then
  SQL_OUT="$(run_sqlplus "$LIST_SQL" || true)"
elif [[ "$MODE" == "datafiles" ]]; then
  [[ -n "$TS_NAME" ]] || { printf '%s\n' '{"ok":false,"error":"tablespace adi gerekli"}'; exit 0; }
  SQL_OUT="$(run_sqlplus "$DATAFILES_SQL" || true)"
elif [[ "$MODE" == "add" ]]; then
  [[ -n "$TS_NAME" && -n "$FILE_PATH" && -n "$SIZE_MB" ]] || {
    printf '%s\n' '{"ok":false,"error":"tablespace, dosya yolu ve boyut gerekli"}'
    exit 0
  }
  if [[ "$FILE_PATH" != /* ]]; then
    printf '%s\n' '{"ok":false,"error":"Dosya yolu / ile baslamali"}'
    exit 0
  fi
  if [[ "$FILE_PATH" != *.dbf && "$FILE_PATH" != *.DBF ]]; then
    printf '%s\n' '{"ok":false,"error":"Dosya adi .dbf ile bitmeli"}'
    exit 0
  fi
  SAFE_PATH="${FILE_PATH//\'/\'\'}"
  TS_UPPER="$(echo "$TS_NAME" | tr '[:lower:]' '[:upper:]')"
  if [[ ! "$TS_UPPER" =~ ^[A-Z0-9_#$]+$ ]]; then
    printf '%s\n' '{"ok":false,"error":"Gecersiz tablespace adi"}'
    exit 0
  fi
  if [[ ! "$SIZE_MB" =~ ^[0-9]+$ ]]; then
    printf '%s\n' '{"ok":false,"error":"Boyut sayi olmali (MB)"}'
    exit 0
  fi
  ADD_SQL="ALTER TABLESPACE ${TS_UPPER} ADD DATAFILE '${SAFE_PATH}' SIZE ${SIZE_MB}M"
  AUTO_LC="$(echo "$AUTO_EXT" | tr '[:upper:]' '[:lower:]')"
  if [[ "$AUTO_LC" == "yes" ]]; then
    NEXT_VAL="${NEXT_MB:-100}"
    MAX_VAL="${MAX_SIZE:-UNLIMITED}"
    if [[ ! "$NEXT_VAL" =~ ^[0-9]+$ ]]; then
      printf '%s\n' '{"ok":false,"error":"NEXT boyutu sayi olmali"}'
      exit 0
    fi
    if [[ "$(echo "$MAX_VAL" | tr '[:upper:]' '[:lower:]')" == "unlimited" ]]; then
      ADD_SQL="${ADD_SQL} AUTOEXTEND ON NEXT ${NEXT_VAL}M MAXSIZE UNLIMITED"
    elif [[ "$MAX_VAL" =~ ^[0-9]+$ ]]; then
      ADD_SQL="${ADD_SQL} AUTOEXTEND ON NEXT ${NEXT_VAL}M MAXSIZE ${MAX_VAL}M"
    else
      printf '%s\n' '{"ok":false,"error":"MAXSIZE sayi veya UNLIMITED olmali"}'
      exit 0
    fi
  fi
  ADD_SQL="${ADD_SQL};"
  export FILE_PATH="$FILE_PATH"
  SQL_OUT="$(run_sqlplus "$ADD_SQL" || true)"
else
  printf '%s\n' '{"ok":false,"error":"gecersiz mod"}'
  exit 0
fi

if echo "$SQL_OUT" | grep -qiE 'ORA-|SP2-'; then
  ERROR="$(echo "$SQL_OUT" | grep -oiE 'ORA-[0-9]+:.*|SP2-[0-9]+:.*' | head -1 | tr -d '\r')"
  [[ -z "$ERROR" ]] && ERROR="Oracle tablespace sorgusu basarisiz"
else
  while IFS= read -r line; do
    line="$(echo "$line" | tr -d '\r')"
    [[ -z "$line" ]] && continue
    ROWS+=("$line")
  done < <(echo "$SQL_OUT" | sed '/^$/d')
fi

TMP_ROWS="$(mktemp)"
printf '%s\n' "${ROWS[@]:-}" >"$TMP_ROWS"
export MODE SID TS_NAME ERROR ROWS_FILE="$TMP_ROWS" FILE_PATH="${FILE_PATH:-}"
PYBIN="$(command -v python3 2>/dev/null || command -v python 2>/dev/null)"
if [[ -z "$PYBIN" ]]; then
  printf '%s\n' '{"ok":false,"error":"python bulunamadi"}'
  rm -f "$TMP_ROWS"
  exit 0
fi
"$PYBIN" - <<'PY'
import json, os, sys

mode = os.environ["MODE"]
sid = os.environ["SID"]
ts = os.environ.get("TS_NAME", "")
error = os.environ.get("ERROR", "")
rows_file = os.environ["ROWS_FILE"]
lines = []
with open(rows_file, "rb") as f:
    for raw in f:
        line = raw.decode("utf-8", "replace").strip()
        if line:
            lines.append(line)

def split_row(line):
    return [p.strip() for p in line.split("|")]

def num(s, default=0):
    try:
        return float(str(s).replace(",", "."))
    except (TypeError, ValueError):
        return default

out = {"ok": not bool(error), "oracle_sid": sid, "error": error}
if error:
    print(json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)

if mode == "add":
    out["message"] = "Datafile eklendi: " + os.environ.get("FILE_PATH", "")
    print(json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)

if mode == "list":
    items = []
    for line in lines:
        p = split_row(line)
        if len(p) < 15:
            continue
        items.append({
            "name": p[0],
            "contents": p[1],
            "status": p[2],
            "size_gb": num(p[3]),
            "free_gb": num(p[4]),
            "used_gb": num(p[5]),
            "used_pct": int(num(p[6])),
            "max_gb": num(p[7]),
            "used_of_max_pct": int(num(p[8])),
            "block_size": int(num(p[9], 8192)),
            "bigfile": p[10],
            "extent_management": p[11],
            "allocation_type": p[12],
            "segment_space_management": p[13],
            "logging": p[14],
        })
    out["tablespaces"] = items
elif mode == "datafiles":
    items = []
    for line in lines:
        p = split_row(line)
        if len(p) < 12:
            continue
        items.append({
            "file_name": p[0],
            "file_id": int(num(p[1])),
            "usage_pct": int(num(p[2])),
            "size_gb": num(p[3]),
            "used_gb": num(p[4]),
            "free_gb": num(p[5]),
            "blocks": int(num(p[6])),
            "auto_extend": p[7].upper() == "YES",
            "increment_mb": int(num(p[8])),
            "max_size": p[9],
            "status": p[10],
            "fragmentation_index": num(p[11]),
        })
    out["tablespace"] = ts
    out["datafiles"] = items

print(json.dumps(out, ensure_ascii=False))
PY
rm -f "$TMP_ROWS"
