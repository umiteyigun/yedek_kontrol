#!/bin/bash
# Yedek asama durumu — .backup-status.json guncelleme (root + oracle).
BACKUP_STATUS_FILE="${BACKUP_STATUS_FILE:-/yedek/orayedek/.backup-status.json}"

bs_update() {
  python3 - "$BACKUP_STATUS_FILE" "$@" <<'PY'
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
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}

def save(data):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)

def parse_ts(value):
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None

def duration_label(sec):
    if sec is None or sec < 0:
        return ""
    sec = int(sec)
    if sec < 60:
        return f"{sec} sn"
    minutes, seconds = divmod(sec, 60)
    if minutes < 60:
        return f"{minutes} dk" + (f" {seconds} sn" if seconds else "")
    hours, minutes = divmod(minutes, 60)
    return f"{hours} sa {minutes} dk"

def end_stage(data, stage_name, now):
    stages = data.setdefault("stages", {})
    stage = stages.get(stage_name, {})
    if stage.get("started_at") and not stage.get("ended_at"):
        stage["ended_at"] = now
        start = parse_ts(stage.get("started_at"))
        end = parse_ts(now)
        if start and end:
            sec = int((end - start).total_seconds())
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
now = datetime.now().astimezone().isoformat(timespec="seconds")
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
        sec = int((datetime.now().astimezone() - started).total_seconds())
        data["total_duration_sec"] = sec
        data["total_duration_label"] = duration_label(sec)
else:
    sys.exit(1)

save(data)
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

bs_ensure_writable() {
  chown oracle:oinstall "$BACKUP_STATUS_FILE" 2>/dev/null \
    || chown oracle:dba "$BACKUP_STATUS_FILE" 2>/dev/null \
    || true
  chmod 664 "$BACKUP_STATUS_FILE" 2>/dev/null || true
}
