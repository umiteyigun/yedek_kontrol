#!/bin/bash
# systemd ExecStart/ExecStop icin yedek-docker stack kontrolu
set -euo pipefail

ROOT="${YEDEK_DOCKER_ROOT:-${YEDEK_ROOT:-/opt/yedek_kontrol}}"
cd "$ROOT"

compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
  else
    echo "HATA: docker compose bulunamadi" >&2
    exit 1
  fi
}

CMD=$(compose_cmd)

case "${1:-start}" in
  start)
    $CMD up -d --remove-orphans
    ;;
  stop)
    $CMD down
    ;;
  restart)
    $CMD down || true
    $CMD up -d --remove-orphans
    ;;
  status)
    $CMD ps
    ;;
  *)
    echo "Kullanim: $0 {start|stop|restart|status}" >&2
    exit 1
    ;;
esac
