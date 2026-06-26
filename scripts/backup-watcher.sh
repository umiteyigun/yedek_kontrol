#!/bin/bash
set -euo pipefail

TRIGGER="/yedek/config/backup.trigger"
LOCK="/yedek/orayedek/.backup-running"

while true; do
  if [ -f "$TRIGGER" ]; then
    TIP="$(tr -d '[:space:]' <"$TRIGGER")"
    rm -f "$TRIGGER"
    if [ -f "$LOCK" ]; then
      echo "$(date -Iseconds) atlandi: zaten calisiyor" >>/yedek/orayedek/backup-watcher.log
      sleep 2
      continue
    fi
    touch "$LOCK"
    echo "$(date -Iseconds) baslatiliyor tip=$TIP" >>/yedek/orayedek/backup-watcher.log
    RAW_TIP="$TIP"
    if [[ "$RAW_TIP" == *:* ]]; then
      RAW_TIP="${RAW_TIP%%:*}"
    fi
    if [[ "$RAW_TIP" == RMAN_* ]]; then
      RUNNER="/yedek/config/run-rman.sh"
    else
      RUNNER="/yedek/config/run-backup.sh"
    fi
    "$RUNNER" "$TIP" || true
    rm -f "$LOCK"
  fi
  sleep 2
done
