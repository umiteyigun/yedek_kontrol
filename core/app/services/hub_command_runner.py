"""Merkez hub toplu komutlari — onayli, guvenli islemler (reboot/halt yok)."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config.models import InstanceSettings, YedekSettings
from app.services import backups as backup_service
from app.services.disk_report import collect_disk_areas
from app.services.ftp_client import browse_directory
from app.services.notifications import NotificationService
from app.services.oracle_probe import is_instance_running, probe_instance

logger = logging.getLogger(__name__)

HOST_OUTPUT = Path(os.getenv("HOST_OUTPUT", "/host-output"))
YEDEK_DIR = Path(os.getenv("YEDEK_DIR", "/yedek/orayedek"))
CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/yedek/config"))

BLOCKED_TOKENS = frozenset(
    {
        "reboot",
        "shutdown",
        "halt",
        "poweroff",
        "init",
        "rm",
        "mkfs",
        "dd",
        "killall",
    }
)

ORACLE_USER_RE = re.compile(r"^[A-Z][A-Z0-9_$#]{0,29}$")


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _run_as_oracle(oracle_sid: str, shell_body: str, timeout: int = 120) -> tuple[int, str, str]:
    sid = (oracle_sid or "orcl").strip()
    script = f"""set -e
export ORACLE_SID={sid!r}
if [[ -f /etc/oratab ]]; then
  ORACLE_HOME="$(awk -F: -v s="$ORACLE_SID" 'tolower($1)==tolower(s) && $0 !~ /^#/ {{print $2; exit}}' /etc/oratab)"
  export ORACLE_HOME
fi
if [[ -z "${{ORACLE_HOME:-}}" || ! -x "${{ORACLE_HOME}}/bin/sqlplus" ]]; then
  ORACLE_HOME="$(ls -d /u01/app/oracle/product/*/db* 2>/dev/null | head -1)"
  export ORACLE_HOME
fi
{shell_body}
"""
    cmd = ["nsenter", "-t", "1", "-m", "-p", "-i", "--", "su", "-", "oracle", "-s", "/bin/bash"]
    try:
        proc = subprocess.run(
            cmd,
            input=script,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        out = proc.stdout.strip()
        if "Last login:" in out:
            out = out.split("Last login:", 1)[0].strip()
        return proc.returncode, out, proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "Zaman asimi"
    except FileNotFoundError:
        return 127, "", "nsenter bulunamadi"


def _parse_sqlplus_output(out: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    lines: list[str] = []
    for ln in out.splitlines():
        clean = ln.strip()
        if not clean:
            continue
        if clean.startswith("Last login:"):
            continue
        if re.match(r"^[\s\-|]+$", clean):
            continue
        lines.append(clean)
    if not lines:
        return rows
    if len(lines) == 1:
        return [{ "value": lines[0] }]

    header_line = lines[0]
    if "|" in header_line:
        headers = [h.strip() for h in header_line.split("|")]
        for line in lines[1:]:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) == len(headers):
                rows.append(dict(zip(headers, parts)))
        return rows

    header = header_line.strip()
    for line in lines[1:]:
        val = line.strip()
        if val:
            rows.append({header: val})
    return rows


def _sqlplus_csv(oracle_sid: str, sql: str, timeout: int = 120) -> tuple[bool, list[dict[str, str]], str]:
    sql_clean = sql.strip().rstrip(";")
    body = f'''
"$ORACLE_HOME/bin/sqlplus" -s / as sysdba <<'SQLEOF'
whenever sqlerror exit sql.sqlcode
set pagesize 5000 feedback off heading on trimspool on linesize 4000 colsep '|'
{sql_clean};
exit
SQLEOF
'''
    code, out, err = _run_as_oracle(oracle_sid, body, timeout=timeout)
    if code != 0:
        return False, [], err or out or f"sqlplus exit {code}"
    rows = _parse_sqlplus_output(out)
    return True, rows, ""


def _oracle_shell(inst: InstanceSettings, shell_body: str, timeout: int = 120) -> tuple[int, str, str]:
    return _run_as_oracle(inst.oracle_sid, shell_body, timeout=timeout)


def _command_oracle_adr_diag_info(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT name, value
FROM v$diag_info
WHERE name IN (
  'ADR Base', 'ADR Home', 'Diag Trace', 'Diag Alert', 'Diag Incident',
  'Diag Cdump', 'Diag Health Monitor', 'Diag Background Dump', 'Default Trace File',
  'Active Problem Count', 'Active Incident Count'
)
ORDER BY name
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    paths = {r.get("NAME") or r.get("name"): r.get("VALUE") or r.get("value") for r in rows}
    return {"diag_info": rows, "paths": paths}


def _command_oracle_trace_parameters(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT name, value, description
FROM v$parameter
WHERE lower(name) LIKE '%trace%'
   OR lower(name) LIKE '%dump%'
   OR name IN ('statistics_level', 'max_dump_file_size', 'timed_statistics')
ORDER BY name
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    return {"parameter_count": len(rows), "parameters": rows}


def _command_oracle_trace_files_report(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    body = r'''
TRACE_DIR=$("$ORACLE_HOME/bin/sqlplus" -s /nolog <<'EOF'
set heading off feedback off pages 0 trimspool on linesize 4000
conn / as sysdba
select value from v$diag_info where name='Diag Trace';
exit
EOF
)
TRACE_DIR=$(echo "$TRACE_DIR" | tr -d '[:space:]')
if [[ -z "$TRACE_DIR" || ! -d "$TRACE_DIR" ]]; then
  echo "ERROR|trace dizini bulunamadi|$TRACE_DIR"
  exit 0
fi
TOTAL=$(find "$TRACE_DIR" -maxdepth 1 -type f \( -name '*.trc' -o -name '*.trm' \) 2>/dev/null | wc -l | tr -d ' ')
echo "SUMMARY|total_files|$TOTAL"
find "$TRACE_DIR" -maxdepth 1 -type f \( -name '*.trc' -o -name '*.trm' \) -printf '%T@|%s|%f\n' 2>/dev/null \
  | sort -t'|' -k1 -rn | head -25 | while IFS='|' read -r ts size name; do
      human=$(date -d "@${ts%.*}" '+%Y-%m-%d %H:%M' 2>/dev/null || echo "$ts")
      echo "FILE|$human|${size}|$name"
    done
'''
    code, out, err = _oracle_shell(inst, body, timeout=90)
    if code not in (0, 124) and not out:
        raise ValueError(err or f"trace listesi alinamadi (exit {code})")
    files: list[dict[str, Any]] = []
    total_files = 0
    trace_dir = ""
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 2:
            continue
        kind = parts[0]
        if kind == "ERROR":
            return {"ok": False, "error": parts[1], "trace_dir": parts[2] if len(parts) > 2 else ""}
        if kind == "SUMMARY" and len(parts) >= 3:
            total_files = int(parts[2] or 0)
        elif kind == "FILE" and len(parts) >= 4:
            files.append(
                {"modified": parts[1], "size_bytes": int(parts[2] or 0), "name": parts[3]}
            )
    if not trace_dir:
        ok, rows, sql_err = _sqlplus_csv(
            inst.oracle_sid, "SELECT value FROM v$diag_info WHERE name='Diag Trace'"
        )
        if ok and rows:
            trace_dir = rows[0].get("VALUE") or rows[0].get("value") or ""
    return {
        "trace_dir": trace_dir,
        "total_trace_files": total_files,
        "recent_files": files,
    }


def _command_oracle_alert_log_tail(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    body = r'''
ALERT_DIR=$("$ORACLE_HOME/bin/sqlplus" -s /nolog <<'EOF'
set heading off feedback off pages 0 trimspool on linesize 4000
conn / as sysdba
select value from v$diag_info where name='Diag Alert';
exit
EOF
)
ALERT_DIR=$(echo "$ALERT_DIR" | tr -d '[:space:]')
TARGET=""
if [[ -n "$ALERT_DIR" && -d "$ALERT_DIR" ]]; then
  if [[ -f "$ALERT_DIR/log.xml" ]]; then
    TARGET="$ALERT_DIR/log.xml"
  else
    TARGET=$(ls -1t "$ALERT_DIR"/alert_*.log 2>/dev/null | head -1)
  fi
fi
if [[ -z "$TARGET" || ! -f "$TARGET" ]]; then
  echo "ALERT_LOG_NOT_FOUND"
  exit 0
fi
echo "ALERT_PATH|$TARGET"
tail -n 50 "$TARGET"
'''
    code, out, err = _oracle_shell(inst, body, timeout=90)
    if code == 124:
        raise ValueError("Zaman asimi")
    if out.strip() == "ALERT_LOG_NOT_FOUND":
        return {"ok": False, "error": "Alert log dosyasi bulunamadi"}
    lines = out.splitlines()
    alert_path = ""
    tail_lines: list[str] = []
    for line in lines:
        if line.startswith("ALERT_PATH|"):
            alert_path = line.split("|", 1)[1]
        else:
            tail_lines.append(line)
    text = "\n".join(tail_lines).strip()
    if len(text) > 12000:
        text = text[-12000:]
    return {"alert_log": alert_path, "line_count": len(tail_lines), "tail": text}


def _command_oracle_trace_disk_usage(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    body = r'''
for LABEL in "Diag Trace" "Diag Alert" "Diag Incident" "Diag Cdump"; do
  DIR=$("$ORACLE_HOME/bin/sqlplus" -s /nolog <<EOF
set heading off feedback off pages 0 trimspool on linesize 4000
conn / as sysdba
select value from v\$diag_info where name='${LABEL}';
exit
EOF
)
  DIR=$(echo "$DIR" | tr -d '[:space:]')
  if [[ -z "$DIR" || ! -d "$DIR" ]]; then
    echo "$LABEL|MISSING|0|0|0"
    continue
  fi
  SIZE=$(du -sb "$DIR" 2>/dev/null | awk '{print $1}')
  FILES=$(find "$DIR" -type f 2>/dev/null | wc -l | tr -d ' ')
  OLD=$(find "$DIR" -type f -mtime +14 2>/dev/null | wc -l | tr -d ' ')
  OLDSZ=$(find "$DIR" -type f -mtime +14 -printf '%s\n' 2>/dev/null | awk '{s+=$1} END {print s+0}')
  echo "$LABEL|$DIR|${SIZE:-0}|$FILES|${OLD:-0}|${OLDSZ:-0}"
done
'''
    code, out, err = _oracle_shell(inst, body, timeout=120)
    if code not in (0, 124) and not out.strip():
        raise ValueError(err or "ADR disk raporu alinamadi")
    areas: list[dict[str, Any]] = []
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) < 5:
            continue
        areas.append(
            {
                "label": parts[0],
                "path": parts[1],
                "size_bytes": int(parts[2] or 0),
                "file_count": int(parts[3] or 0),
                "files_older_14d": int(parts[4] or 0),
                "bytes_older_14d": int(parts[5] or 0) if len(parts) > 5 else 0,
            }
        )
    return {"areas": areas}


def _command_oracle_active_trace_sessions(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT * FROM (
  SELECT s.sid, s.serial#, s.username, s.program, s.module, s.action,
         s.sql_id, p.tracefile, s.logon_time
  FROM v$session s
  JOIN v$process p ON s.paddr = p.addr
  WHERE s.type != 'BACKGROUND'
    AND s.username IS NOT NULL
    AND (p.tracefile IS NOT NULL OR s.sql_trace = 'ENABLED')
  ORDER BY s.logon_time DESC
) WHERE ROWNUM <= 30
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    return {"active_trace_sessions": len(rows), "sessions": rows}


def _command_oracle_blocking_sessions(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT blocking.sid blocker_sid,
       blocking.serial# blocker_serial,
       blocking.username blocker_user,
       blocked.sid blocked_sid,
       blocked.serial# blocked_serial,
       blocked.username blocked_user,
       blocked.program blocked_program,
       blocked.sql_id blocked_sql_id
FROM v$session blocked
JOIN v$session blocking ON blocking.sid = blocked.blocking_session
WHERE blocked.blocking_session IS NOT NULL
ORDER BY blocking.sid
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    return {"blocking_count": len(rows), "blocks": rows}


def _command_oracle_redo_log_status(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT l.group#, l.thread#, l.sequence#, l.bytes/1024/1024 size_mb,
       l.members, l.status, l.archived, l.first_change#
FROM v$log l
ORDER BY l.group#
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    return {"redo_groups": rows}


def _command_oracle_archive_dest_status(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT dest_id, dest_name, status, destination, log_sequence, error
FROM v$archive_dest
WHERE status != 'INACTIVE'
ORDER BY dest_id
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        sql = """
SELECT dest_id, dest_name, status, destination
FROM v$archive_dest_status
WHERE status != 'INACTIVE'
ORDER BY dest_id
"""
        ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    return {"destinations": rows}


def _command_oracle_undo_usage(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT tablespace_name, status,
       ROUND(used_percent,1) used_pct,
       ROUND(max_query_len/60,1) max_query_min
FROM v$undostat
WHERE begin_time = (SELECT MAX(begin_time) FROM v$undostat)
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        sql = """
SELECT tablespace_name, ROUND(used_percent,1) used_pct
FROM dba_tablespace_usage_metrics
WHERE tablespace_name LIKE '%UNDO%'
"""
        ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    return {"undo": rows}


def _command_oracle_listener_status(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    body = r'''
if [[ -x "$ORACLE_HOME/bin/lsnrctl" ]]; then
  "$ORACLE_HOME/bin/lsnrctl" status 2>&1 | head -40
else
  echo "LSNRCTL_NOT_FOUND"
fi
'''
    code, out, err = _oracle_shell(inst, body, timeout=45)
    text = out or err
    if "LSNRCTL_NOT_FOUND" in text:
        return {"ok": False, "error": "lsnrctl bulunamadi"}
    if code != 0 and not out:
        raise ValueError(err or f"lsnrctl exit {code}")
    if len(text) > 8000:
        text = text[:8000]
    return {"status_text": text.strip()}


def _sql_or_fallback(
    inst: InstanceSettings,
    primary_sql: str,
    fallback_sql: str,
    *,
    timeout: int = 120,
) -> tuple[list[dict[str, str]], str, str | None]:
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, primary_sql, timeout=timeout)
    if ok:
        return rows, "primary", None
    ok2, rows2, err2 = _sqlplus_csv(inst.oracle_sid, fallback_sql, timeout=timeout)
    if ok2:
        return rows2, "fallback", err or err2
    raise ValueError(err2 or err or "SQL basarisiz")


def _command_oracle_ash_recent(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    ash_sql = """
SELECT * FROM (
  SELECT TO_CHAR(sample_time,'YYYY-MM-DD HH24:MI') sample_min,
         session_id sid, sql_id, event, wait_class, session_state
  FROM v$active_session_history
  WHERE sample_time > SYSDATE - 30/1440
  ORDER BY sample_time DESC
) WHERE ROWNUM <= 30
"""
    fallback = """
SELECT * FROM (
  SELECT sid, serial#, username, sql_id, event, state status, wait_class
  FROM v$session
  WHERE type != 'BACKGROUND' AND username IS NOT NULL
    AND (state = 'WAITING' OR status = 'ACTIVE')
  ORDER BY last_call_et DESC
) WHERE ROWNUM <= 30
"""
    rows, source, note = _sql_or_fallback(inst, ash_sql, fallback)
    out: dict[str, Any] = {"source": "ash" if source == "primary" else "v$session", "samples": rows}
    if note:
        out["ash_note"] = "ASH kullanilamadi, anlik oturumlar gosterildi"
    return out


def _command_oracle_ash_top_events(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    ash_sql = """
SELECT * FROM (
  SELECT event, wait_class, COUNT(*) samples
  FROM v$active_session_history
  WHERE sample_time > SYSDATE - 1/24
    AND session_state = 'WAITING'
  GROUP BY event, wait_class
  ORDER BY samples DESC
) WHERE ROWNUM <= 20
"""
    fallback = """
SELECT * FROM (
  SELECT event, wait_class, total_waits, ROUND(time_waited_micro/1000000,1) time_waited_sec
  FROM v$system_event
  WHERE wait_class NOT IN ('Idle')
  ORDER BY time_waited_micro DESC
) WHERE ROWNUM <= 20
"""
    rows, source, note = _sql_or_fallback(inst, ash_sql, fallback)
    return {
        "source": "ash" if source == "primary" else "v$system_event",
        "events": rows,
        "ash_note": note,
    }


def _command_oracle_ash_top_sql(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    ash_sql = """
SELECT * FROM (
  SELECT sql_id, COUNT(*) samples,
         TO_CHAR(MAX(sample_time),'YYYY-MM-DD HH24:MI') last_seen
  FROM v$active_session_history
  WHERE sample_time > SYSDATE - 1/24 AND sql_id IS NOT NULL
  GROUP BY sql_id
  ORDER BY samples DESC
) WHERE ROWNUM <= 20
"""
    fallback = """
SELECT * FROM (
  SELECT sql_id, executions, ROUND(elapsed_time/1000000,1) elapsed_sec, buffer_gets
  FROM v$sql
  WHERE executions > 0 AND sql_id IS NOT NULL
  ORDER BY elapsed_time DESC
) WHERE ROWNUM <= 20
"""
    rows, source, note = _sql_or_fallback(inst, ash_sql, fallback)
    return {
        "source": "ash" if source == "primary" else "v$sql",
        "sql_stats": rows,
        "ash_note": note,
    }


def _command_oracle_awr_snapshots(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT * FROM (
  SELECT snap_id,
         TO_CHAR(begin_interval_time,'YYYY-MM-DD HH24:MI') begin_at,
         TO_CHAR(end_interval_time,'YYYY-MM-DD HH24:MI') end_at,
         ROUND((CAST(end_interval_time AS DATE) - CAST(begin_interval_time AS DATE)) * 24 * 60, 1) duration_min
  FROM dba_hist_snapshot
  ORDER BY snap_id DESC
) WHERE ROWNUM <= 15
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql, timeout=180)
    if not ok:
        return {
            "ok": False,
            "error": err,
            "hint": "AWR lisans/pack veya STATISTICS_LEVEL=TYPICAL gerekebilir",
        }
    return {"snapshot_count": len(rows), "snapshots": rows}


def _command_oracle_awr_db_time(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT stat_name, ROUND(value/1000000, 2) value_sec
FROM dba_hist_sys_time_model
WHERE snap_id = (SELECT MAX(snap_id) FROM dba_hist_snapshot)
  AND stat_name IN (
    'DB time', 'DB CPU', 'sql execute elapsed time',
    'parse time elapsed', 'PL/SQL execution elapsed time'
  )
ORDER BY value_sec DESC
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql, timeout=180)
    if not ok:
        sql2 = """
SELECT stat_name, ROUND(value/1000000, 2) value_sec
FROM v$sys_time_model
WHERE stat_name IN (
  'DB time', 'DB CPU', 'sql execute elapsed time',
  'parse time elapsed', 'PL/SQL execution elapsed time'
)
ORDER BY value_sec DESC
"""
        ok2, rows2, err2 = _sqlplus_csv(inst.oracle_sid, sql2)
        if not ok2:
            raise ValueError(err or err2)
        return {"source": "v$sys_time_model", "metrics": rows2}
    return {"source": "dba_hist_sys_time_model", "metrics": rows}


def _command_oracle_system_wait_events(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT * FROM (
  SELECT wait_class, event, total_waits,
         ROUND(time_waited_micro/1000000, 1) time_waited_sec
  FROM v$system_event
  WHERE wait_class NOT IN ('Idle')
  ORDER BY time_waited_micro DESC
) WHERE ROWNUM <= 25
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    return {"events": rows}


def _command_disk_report(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    path = inst.effective_directorydizini(settings.yedek_dir)
    disks = collect_disk_areas(path)
    return {"yedek_path": path, **disks}


def _command_backup_inventory(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    items = backup_service.list_backups(settings, inst, limit=10)
    return {
        "count": len(items),
        "backups": [
            {"name": it.archive_name, "size_mb": it.size_mb, "mtime": it.mtime}
            for it in items
        ],
    }


def _command_oracle_connectivity(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    running = is_instance_running(inst.oracle_sid)
    probe = probe_instance(inst.oracle_sid, settings.yedek_dir)
    return {
        "oracle_sid": inst.oracle_sid,
        "process_running": running,
        "probe_ok": probe.ok,
        "probe_error": probe.error,
        "oracle_version": probe.oracle_version_full or probe.oracle_ver,
        "directory_path": probe.directory_path,
    }


def _command_oracle_user_expiry(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT username, TO_CHAR(expiry_date,'YYYY-MM-DD') expiry_date, account_status
FROM dba_users
WHERE username IN ('AKILU','SYSTEM','SYS')
   OR expiry_date < SYSDATE + 60
ORDER BY expiry_date NULLS LAST, username
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    return {"users": rows}


def _command_oracle_tablespace_usage(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT * FROM (
  SELECT tablespace_name, ROUND(used_percent,1) used_pct
  FROM dba_tablespace_usage_metrics
  ORDER BY used_percent DESC
) WHERE ROWNUM <= 15
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    return {"tablespaces": rows}


def _command_oracle_invalid_objects(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT owner, object_type, COUNT(*) cnt
FROM dba_objects
WHERE status = 'INVALID'
GROUP BY owner, object_type
ORDER BY cnt DESC
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    total = sum(int(r.get("CNT") or r.get("cnt") or 0) for r in rows)
    return {"invalid_total": total, "breakdown": rows}


def _command_oracle_archivelog_mode(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = "SELECT log_mode FROM v$database"
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    mode = rows[0].get("LOG_MODE") or rows[0].get("log_mode") if rows else ""
    return {"log_mode": mode}


def _command_oracle_session_count(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT status, COUNT(*) cnt FROM v$session GROUP BY status ORDER BY cnt DESC
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql)
    if not ok:
        raise ValueError(err)
    return {"sessions": rows}


def _command_oracle_fk_missing_index(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    sql = """
SELECT * FROM (
  SELECT c.owner, c.table_name, cc.column_name
  FROM dba_constraints c
  JOIN dba_cons_columns cc ON c.owner = cc.owner AND c.constraint_name = cc.constraint_name
  WHERE c.constraint_type = 'R'
    AND NOT EXISTS (
      SELECT 1 FROM dba_ind_columns ic
      JOIN dba_indexes i ON ic.index_name = i.index_name AND ic.owner = i.owner
      WHERE ic.table_owner = cc.owner AND ic.table_name = cc.table_name
        AND ic.column_name = cc.column_name AND i.index_type != 'LOB'
    )
  ORDER BY c.owner, c.table_name, cc.position
) WHERE ROWNUM <= 50
"""
    ok, rows, err = _sqlplus_csv(inst.oracle_sid, sql, timeout=180)
    if not ok:
        raise ValueError(err)
    return {"missing_index_fk_count": len(rows), "rows": rows}


def _command_oracle_password_change(
    settings: YedekSettings,
    inst: InstanceSettings,
    params: dict[str, Any],
) -> dict[str, Any]:
    username = str(params.get("username") or "").strip().upper()
    new_password = str(params.get("new_password") or "")
    if not ORACLE_USER_RE.match(username):
        raise ValueError("Gecersiz Oracle kullanici adi")
    if len(new_password) < 8:
        raise ValueError("Sifre en az 8 karakter olmali")
    if any(tok in new_password.lower() for tok in BLOCKED_TOKENS):
        raise ValueError("Sifre gecersiz")
    # sqlplus identifier quoting
    esc = new_password.replace('"', '""')
    body = f'''
"$ORACLE_HOME/bin/sqlplus" -s /nolog <<'SQLEOF'
whenever sqlerror exit sql.sqlcode
conn / as sysdba
ALTER USER {username} IDENTIFIED BY "{esc}";
exit
SQLEOF
'''
    code, out, err = _run_as_oracle(inst.oracle_sid, body)
    if code != 0:
        raise ValueError(err or out or f"ALTER USER basarisiz (exit {code})")
    return {"username": username, "changed": True}


def _notifications_for_instance(inst: InstanceSettings) -> list[dict[str, Any]]:
    svc = NotificationService(CONFIG_DIR)
    items = svc.recent(80)
    out: list[dict[str, Any]] = []
    for it in items:
        iid = str(it.get("InstanceId") or "")
        kurum = str(inst.kurumkodu or "")
        if iid and iid == inst.id:
            out.append(it)
        elif not iid and kurum and str(it.get("KurumNo") or "") == kurum:
            out.append(it)
        if len(out) >= 10:
            break
    return out


def _notification_summary(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not items:
        return None
    last = items[0]
    return {
        "tarih": last.get("Tarih"),
        "dosya": last.get("DosyaAdi"),
        "boyut": last.get("YedekBoyutu"),
        "ftp": last.get("Ftp"),
        "mail": last.get("Mail"),
        "received_at": last.get("received_at"),
    }


def _command_transmission_status(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    ftp_host, ftp_user, _ftp_pass = inst.effective_ftp(settings)
    recent = _notifications_for_instance(inst)
    return {
        "ftp_upload_enabled": inst.ftp_upload_enabled,
        "ftp_host": ftp_host,
        "ftp_user": ftp_user,
        "ftp_dir": inst.localftpdir or "/",
        "remote_api_url": settings.remote_api_url,
        "mail_notify": settings.mail_notify,
        "kurumkodu": inst.kurumkodu,
        "last_notification": _notification_summary(recent),
        "recent_notification_count": len(recent),
    }


def _command_transmission_ftp_test(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    if not inst.ftp_upload_enabled:
        return {"ok": False, "skipped": True, "reason": "Uzak FTP yuklemesi bu kurumda kapali"}
    ftp_host, ftp_user, ftp_pass = inst.effective_ftp(settings)
    if not str(ftp_host).strip():
        raise ValueError("FTP sunucu adresi tanimli degil")
    if not str(ftp_user).strip():
        raise ValueError("FTP kullanici adi tanimli degil")
    if not str(ftp_pass).strip():
        raise ValueError("FTP sifresi tanimli degil")
    result = browse_directory(
        ftp_host,
        ftp_user,
        ftp_pass,
        inst.localftpdir or "/",
        margin_pct=settings.backup_size_margin_pct,
    )
    analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
    entries = result.get("entries") or []
    backup_entries = [e for e in entries if isinstance(e, dict) and e.get("is_backup")]
    return {
        "ok": True,
        "host": result.get("host"),
        "port": result.get("port"),
        "path": result.get("path"),
        "entry_count": len(entries),
        "backup_file_count": analysis.get("backup_count") or len(backup_entries),
        "latest_backup": analysis.get("latest_name"),
        "latest_backup_size": analysis.get("latest_size"),
    }


def _command_transmission_api_test(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    url = (settings.remote_api_url or "").strip()
    if not url:
        return {"ok": False, "reason": "Merkezi API URL tanimli degil"}
    started = time.monotonic()
    try:
        with httpx.Client(timeout=15.0, verify=False, follow_redirects=True) as client:
            response = client.get(url, params={"KurumNo": inst.kurumkodu or "0", "Mail": "0", "Ftp": "0"})
        latency_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": response.status_code < 500,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "url": url,
            "kurumkodu": inst.kurumkodu,
        }
    except httpx.HTTPError as exc:
        return {"ok": False, "url": url, "error": str(exc)}


def _command_transmission_notification_history(
    settings: YedekSettings, inst: InstanceSettings
) -> dict[str, Any]:
    items = _notifications_for_instance(inst)
    slim = [
        {
            "tarih": it.get("Tarih"),
            "dosya": it.get("DosyaAdi"),
            "boyut": it.get("YedekBoyutu"),
            "ftp": it.get("Ftp"),
            "mail": it.get("Mail"),
            "yedek_tipi": it.get("YedekTipi"),
            "received_at": it.get("received_at"),
        }
        for it in items
    ]
    return {"count": len(slim), "notifications": slim}


def _command_transmission_ftp_remote_inventory(
    settings: YedekSettings, inst: InstanceSettings
) -> dict[str, Any]:
    report = _command_transmission_ftp_test(settings, inst)
    if report.get("skipped"):
        return report
    if not report.get("ok"):
        return report
    ftp_host, ftp_user, ftp_pass = inst.effective_ftp(settings)
    result = browse_directory(
        ftp_host,
        ftp_user,
        ftp_pass,
        inst.localftpdir or "/",
        margin_pct=settings.backup_size_margin_pct,
    )
    entries = result.get("entries") or []
    backups = [
        {
            "name": e.get("name"),
            "size": e.get("size"),
            "modified": e.get("modified_display") or e.get("modified"),
        }
        for e in entries
        if isinstance(e, dict) and e.get("is_backup")
    ]
    backups.sort(key=lambda row: str(row.get("modified") or ""), reverse=True)
    return {
        "host": result.get("host"),
        "path": result.get("path"),
        "backup_count": len(backups),
        "backups": backups[:15],
    }


def _command_service_health(settings: YedekSettings, inst: InstanceSettings) -> dict[str, Any]:
    def _docker_ps() -> str:
        try:
            proc = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}:{{.Status}}"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            return proc.stdout.strip()
        except OSError:
            return ""

    status_file = YEDEK_DIR / ".backup-status.json"
    backup_status: dict[str, Any] = {}
    if status_file.is_file():
        try:
            backup_status = json.loads(status_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup_status = {}
    return {
        "hostname": settings.hostname,
        "docker": _docker_ps(),
        "backup_status": backup_status,
        "schedule_file": str(HOST_OUTPUT / "schedule.json"),
        "schedule_exists": (HOST_OUTPUT / "schedule.json").is_file(),
    }


COMMAND_HANDLERS: dict[str, Any] = {
    "disk_report": _command_disk_report,
    "backup_inventory": _command_backup_inventory,
    "oracle_connectivity": _command_oracle_connectivity,
    "oracle_user_expiry": _command_oracle_user_expiry,
    "oracle_tablespace_usage": _command_oracle_tablespace_usage,
    "oracle_invalid_objects": _command_oracle_invalid_objects,
    "oracle_archivelog_mode": _command_oracle_archivelog_mode,
    "oracle_session_count": _command_oracle_session_count,
    "oracle_fk_missing_index": _command_oracle_fk_missing_index,
    "oracle_password_change": _command_oracle_password_change,
    "oracle_adr_diag_info": _command_oracle_adr_diag_info,
    "oracle_trace_parameters": _command_oracle_trace_parameters,
    "oracle_trace_files_report": _command_oracle_trace_files_report,
    "oracle_alert_log_tail": _command_oracle_alert_log_tail,
    "oracle_trace_disk_usage": _command_oracle_trace_disk_usage,
    "oracle_active_trace_sessions": _command_oracle_active_trace_sessions,
    "oracle_blocking_sessions": _command_oracle_blocking_sessions,
    "oracle_redo_log_status": _command_oracle_redo_log_status,
    "oracle_archive_dest_status": _command_oracle_archive_dest_status,
    "oracle_undo_usage": _command_oracle_undo_usage,
    "oracle_listener_status": _command_oracle_listener_status,
    "oracle_ash_recent": _command_oracle_ash_recent,
    "oracle_ash_top_events": _command_oracle_ash_top_events,
    "oracle_ash_top_sql": _command_oracle_ash_top_sql,
    "oracle_awr_snapshots": _command_oracle_awr_snapshots,
    "oracle_awr_db_time": _command_oracle_awr_db_time,
    "oracle_system_wait_events": _command_oracle_system_wait_events,
    "service_health": _command_service_health,
    "transmission_status": _command_transmission_status,
    "transmission_ftp_test": _command_transmission_ftp_test,
    "transmission_api_test": _command_transmission_api_test,
    "transmission_notification_history": _command_transmission_notification_history,
    "transmission_ftp_remote_inventory": _command_transmission_ftp_remote_inventory,
}

COMMAND_LABELS: dict[str, str] = {
    "disk_report": "Disk kullanim raporu",
    "backup_inventory": "Son yedekler listesi",
    "oracle_connectivity": "Oracle DB baglanti testi",
    "oracle_user_expiry": "Oracle DB kullanici suresi (60 gun)",
    "oracle_tablespace_usage": "Tablespace doluluk (top 15)",
    "oracle_invalid_objects": "Gecersiz (INVALID) nesneler",
    "oracle_archivelog_mode": "Archive log modu",
    "oracle_session_count": "Aktif oturum sayisi",
    "oracle_fk_missing_index": "FK kolonlarinda eksik index",
    "oracle_password_change": "Oracle DB kullanici sifresi degistir",
    "oracle_adr_diag_info": "ADR / diag dizin yollari",
    "oracle_trace_parameters": "Trace ve dump parametreleri",
    "oracle_trace_files_report": "Son trace dosyalari (.trc/.trm)",
    "oracle_alert_log_tail": "Alert log son 50 satir",
    "oracle_trace_disk_usage": "Trace/alert/incident disk kullanimi",
    "oracle_active_trace_sessions": "Aktif trace acik oturumlar",
    "oracle_blocking_sessions": "Blocking lock oturumlari",
    "oracle_redo_log_status": "Redo log grup durumu",
    "oracle_archive_dest_status": "Archive hedef durumu",
    "oracle_undo_usage": "Undo tablespace kullanimi",
    "oracle_listener_status": "Listener (lsnrctl) durumu",
    "oracle_ash_recent": "ASH son aktivite (30 dk)",
    "oracle_ash_top_events": "ASH top wait event (1 saat)",
    "oracle_ash_top_sql": "ASH top SQL (1 saat)",
    "oracle_awr_snapshots": "AWR snapshot listesi",
    "oracle_awr_db_time": "AWR DB time metrikleri",
    "oracle_system_wait_events": "Sistem wait event ozeti",
    "service_health": "Servis durumu (docker, yedek)",
    "transmission_status": "Iletim ayarlari ozeti",
    "transmission_ftp_test": "Uzak FTP baglanti testi",
    "transmission_api_test": "Merkezi API baglanti testi",
    "transmission_notification_history": "Son yedek bildirimleri",
    "transmission_ftp_remote_inventory": "Uzak FTP yedek envanteri",
}


def list_commands() -> list[dict[str, str]]:
    return [{"key": k, "label": COMMAND_LABELS.get(k, k)} for k in COMMAND_HANDLERS]


def run_command(
    settings: YedekSettings,
    instance_id: str,
    command_key: str,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = (command_key or "").strip().lower()
    if key not in COMMAND_HANDLERS:
        raise ValueError(f"Bilinmeyen komut: {key}")
    for token in BLOCKED_TOKENS:
        if token in key:
            raise ValueError("Guvenlik: komut engellendi")
    inst = settings.get_instance(instance_id) or settings.first_instance()
    if not inst:
        raise ValueError("Instance bulunamadi")
    params = parameters or {}
    handler = COMMAND_HANDLERS[key]
    if key == "oracle_password_change":
        report = handler(settings, inst, params)
    else:
        report = handler(settings, inst)
    return {
        "ok": True,
        "command": key,
        "command_label": COMMAND_LABELS.get(key, key),
        "instance_id": inst.id,
        "oracle_sid": inst.oracle_sid,
        "ran_at": _utcnow(),
        "report": report,
    }
