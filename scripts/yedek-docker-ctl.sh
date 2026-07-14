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
COMPOSE_ARGS=(-f docker-compose.yml)
if [[ -f docker-compose.release.yml ]]; then
  COMPOSE_ARGS+=(-f docker-compose.release.yml)
fi
if [[ -f /yedek/config/docker-compose.volumes.yml ]]; then
  COMPOSE_ARGS+=(-f /yedek/config/docker-compose.volumes.yml)
fi

case "${1:-start}" in
  start)
    bash /yedek/config/ensure-panel-ssl-access.sh 2>/dev/null || true
    $CMD "${COMPOSE_ARGS[@]}" up -d --remove-orphans
    ;;
  stop)
    $CMD "${COMPOSE_ARGS[@]}" down
    ;;
  restart)
    bash /yedek/config/ensure-panel-ssl-access.sh 2>/dev/null || true
    $CMD "${COMPOSE_ARGS[@]}" down || true
    $CMD "${COMPOSE_ARGS[@]}" up -d --remove-orphans
    ;;
  status)
    $CMD "${COMPOSE_ARGS[@]}" ps
    ;;
  *)
    echo "Kullanim: $0 {start|stop|restart|status}" >&2
    exit 1
    ;;
esac
