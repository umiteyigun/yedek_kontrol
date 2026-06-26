#!/bin/sh
set -eu

DAYS="${RETENTION_DAYS:-2}"
DIR="/yedek"

find "$DIR" -type f -mtime "+$DAYS" -name '*.gz' -delete
find "$DIR" -type f -mtime "+$DAYS" -name '*.log' -delete

echo "$(date -Iseconds) retention: deleted files older than ${DAYS} days in ${DIR}"
