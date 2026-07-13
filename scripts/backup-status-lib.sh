#!/bin/bash
# Yedek asama durumu — .backup-status.json guncelleme (root + oracle).
# Python 2.6+ / 3.x ile calisir (Oracle host'larda default siklikla 2.6; total_seconds 2.7+).
# Default path; yedek.sh readonly tanimliyorsa yeniden atama yapma.
if [[ -z "${BACKUP_STATUS_FILE:-}" ]]; then
  BACKUP_STATUS_FILE="/yedek/orayedek/.backup-status.json"
fi

_bs_python() {
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s\n' python
    return 0
  fi
  echo "HATA: python/python3 bulunamadi (backup-status-lib)" >&2
  return 1
}

bs_update() {
  local py
  py="$(_bs_python)" || return 1
  "$py" - "$BACKUP_STATUS_FILE" "$@" <<'PY'
from __future__ import print_function
import io
import json
import os
import sys
from datetime import datetime

path, action = sys.argv[1], sys.argv[2]
extra = sys.argv[3:]


def parse_kv(argv):
    out = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            key = token[2:].replace("-", "_")
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                out[key] = argv[i + 1]
                i += 2
            else:
                out[key] = "1"
                i += 1
        else:
            i += 1
    return out


def load():
    if not os.path.isfile(path):
        return {}
    try:
        with io.open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, IOError, ValueError):
        return {}


def atomic_write(target, payload):
    parent = os.path.dirname(target) or "."
    if not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except TypeError:
            if not os.path.isdir(parent):
                os.makedirs(parent)
    tmp = target + ".tmp"
    if sys.version_info[0] >= 3:
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
    else:
        with open(tmp, "wb") as fh:
            text = json.dumps(payload, ensure_ascii=False)
            if not isinstance(text, bytes):
                text = text.encode("utf-8")
            fh.write(text)
    try:
        os.replace(tmp, target)
    except AttributeError:
        if os.path.exists(target):
            os.remove(target)
        os.rename(tmp, target)


def now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S%z")


def parse_ts(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if hasattr(datetime, "fromisoformat"):
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            chunk = text[:19]
            return datetime.strptime(chunk, fmt)
        except ValueError:
            continue
    return None


def timedelta_total_seconds(td):
    """Python 2.6+ (datetime.timedelta.total_seconds is 2.7+)."""
    try:
        return td.total_seconds()
    except AttributeError:
        return (td.days * 86400) + td.seconds + (td.microseconds / 1.0e6)


def duration_label(sec):
    if sec is None or sec < 0:
        return ""
    sec = int(sec)
    if sec < 60:
        return "{0} sn".format(sec)
    minutes, seconds = divmod(sec, 60)
    if minutes < 60:
        tail = " {0} sn".format(seconds) if seconds else ""
        return "{0} dk{1}".format(minutes, tail)
    hours, minutes = divmod(minutes, 60)
    return "{0} sa {1} dk".format(hours, minutes)


def end_stage(data, stage_name, now):
    stages = data.setdefault("stages", {})
    stage = stages.get(stage_name, {})
    if stage.get("started_at") and not stage.get("ended_at"):
        stage["ended_at"] = now
        start = parse_ts(stage.get("started_at"))
        end = parse_ts(now)
        if start and end:
            sec = int(timedelta_total_seconds(end - start))
            stage["duration_sec"] = sec
            stage["duration_label"] = duration_label(sec)
    stages[stage_name] = stage


def start_stage(data, stage_name, now, instance_id=""):
    stages = data.setdefault("stages", {})
    current = data.get("stage")
    if current and current != stage_name:
        end_stage(data, current, now)
    data["stage"] = stage_name
    data["stage_started_at"] = now
    data["updated_at"] = now
    if instance_id:
        data["instance_id"] = instance_id
    existing = stages.get(stage_name)
    if not existing or existing.get("ended_at"):
        stages[stage_name] = {
            "started_at": now,
            "ended_at": None,
            "duration_sec": None,
            "duration_label": "",
        }
    else:
        existing.setdefault("started_at", now)
        stages[stage_name] = existing
    data["stages"] = stages


kwargs = parse_kv(extra)
now = now_iso()
data = load()

if action == "init":
    data = {
        "state": kwargs.get("state", "running"),
        "stage": kwargs.get("stage", "preflight"),
        "tip": kwargs.get("tip", ""),
        "instance_id": kwargs.get("instance_id", ""),
        "exit_code": int(kwargs.get("exit_code", "0") or 0),
        "log_file": kwargs.get("log_file", ""),
        "started_at": now,
        "stage_started_at": now,
        "updated_at": now,
        "stages": {},
        "backup_kind": kwargs.get("backup_kind", "expdp"),
    }
    start_stage(data, data["stage"], now, data.get("instance_id", ""))
elif action == "stage":
    stage_name = kwargs.get("name") or (extra[0] if extra and not extra[0].startswith("--") else "")
    if not stage_name:
        sys.exit(1)
    instance_id = kwargs.get("instance_id", "")
    data.setdefault("state", "running")
    start_stage(data, stage_name, now, instance_id)
elif action == "finish":
    state = kwargs.get("state", "done")
    exit_code = int(kwargs.get("exit_code", "0") or 0)
    current = data.get("stage")
    if current:
        end_stage(data, current, now)
    data["state"] = state
    data["exit_code"] = exit_code
    data["updated_at"] = now
    started = parse_ts(data.get("started_at"))
    if started:
        sec = int(timedelta_total_seconds(datetime.now() - started))
        data["total_duration_sec"] = sec
        data["total_duration_label"] = duration_label(sec)
elif action == "reason":
    reason = kwargs.get("text") or (extra[0] if extra else "")
    data["reason"] = str(reason).replace("\n", " ")
    data["updated_at"] = now
else:
    sys.exit(1)

atomic_write(path, data)
PY
}

bs_init() {
  bs_update init "$@"
}

bs_stage() {
  local stage_name="$1"
  local instance_id="${2:-}"
  if [[ -n "$instance_id" ]]; then
    bs_update stage "$stage_name" --instance-id "$instance_id"
  else
    bs_update stage "$stage_name"
  fi
}

bs_finish() {
  bs_update finish "$@"
}

bs_set_reason() {
  local reason="$1"
  bs_update reason --text "$reason"
}

bs_ensure_writable() {
  chown oracle:oinstall "$BACKUP_STATUS_FILE" 2>/dev/null \
    || chown oracle:dba "$BACKUP_STATUS_FILE" 2>/dev/null \
    || true
  chmod 664 "$BACKUP_STATUS_FILE" 2>/dev/null || true
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  case "${1:-}" in
    init)
      shift
      bs_init "$@"
      ;;
    stage)
      shift
      bs_stage "$@"
      ;;
    finish)
      shift
      bs_finish "$@"
      ;;
  reason)
      shift
      bs_set_reason "$*"
      ;;
    *)
      echo "Kullanim: $0 init|stage|finish|reason ..." >&2
      exit 1
      ;;
  esac
fi
