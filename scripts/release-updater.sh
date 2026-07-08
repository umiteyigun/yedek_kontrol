#!/bin/bash
# Release updater (image tabanli): core/agent image cek, host scriptleri image'dan cikar,
# health check basarisizsa onceki taga rollback yap.
set -euo pipefail

ROOT="${YEDEK_ROOT:-/opt/yedek_kontrol}"
ENV_FILE="/yedek/config/release-update.env"
STATE_FILE="${ROOT}/config/release-state.json"
LOCK_FILE="/var/run/yedek-release-update.lock"
COMPOSE_OVERRIDE="${ROOT}/docker-compose.release.yml"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi

: "${RELEASE_UPDATER_ENABLED:=0}"
: "${RELEASE_TARGET_TAG:=}"
: "${RELEASE_CORE_IMAGE:=git.trtek.tr/umiteyigun/yedek-core}"
: "${RELEASE_AGENT_IMAGE:=git.trtek.tr/umiteyigun/yedek-central-agent}"
: "${RELEASE_REGISTRY_HOST:=git.trtek.tr}"
: "${RELEASE_READONLY_TOKEN:=}"
: "${RELEASE_REGISTRY_USER:=oauth2}"
: "${RELEASE_SKIP_PULL:=0}"

[[ "$RELEASE_UPDATER_ENABLED" == "1" ]] || exit 0
[[ -n "$RELEASE_TARGET_TAG" ]] || exit 0
[[ -d "$ROOT" ]] || exit 1

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  exit 0
fi

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

write_state() {
  local status="$1"
  local message="$2"
  local current_tag="$3"
  local target_tag="$4"
  local now
  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  local esc_message
  esc_message="$(printf '%s' "$message" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  cat >"${STATE_FILE}.tmp" <<EOF
{"status":"${status}","message":"${esc_message}","current_tag":"${current_tag}","target_tag":"${target_tag}","updated_at":"${now}"}
EOF
  mv -f "${STATE_FILE}.tmp" "$STATE_FILE"
}

read_current_tag() {
  if [[ ! -f "$STATE_FILE" ]]; then
    echo ""
    return 0
  fi
  sed -n 's/.*"current_tag":"\([^"]*\)".*/\1/p' "$STATE_FILE" | head -1
}

deploy_tag() {
  local tag="$1"
  local phase="${2:-deploy}"
  local tmpdir
  tmpdir="$(mktemp -d /tmp/yedek-release.XXXXXX)"
  trap 'rm -rf "$tmpdir"' RETURN

  cat >"$COMPOSE_OVERRIDE" <<EOF
services:
  core:
    image: ${RELEASE_CORE_IMAGE}:${tag}
  central-agent:
    image: ${RELEASE_AGENT_IMAGE}:${tag}
EOF

  # Public compose ${RELEASE_*} degiskenlerini .env'den okur
  ensure_release_env_vars() {
    local envf="${ROOT}/.env"
    touch "$envf"
    for kv in \
      "RELEASE_CORE_IMAGE=${RELEASE_CORE_IMAGE}" \
      "RELEASE_AGENT_IMAGE=${RELEASE_AGENT_IMAGE}" \
      "RELEASE_IMAGE_TAG=${tag}"; do
      local key="${kv%%=*}"
      if grep -q "^${key}=" "$envf" 2>/dev/null; then
        sed -i "s|^${key}=.*|${kv}|" "$envf"
      else
        echo "$kv" >>"$envf"
      fi
    done
  }
  ensure_release_env_vars

  if [[ "$RELEASE_SKIP_PULL" != "1" ]]; then
    docker pull "${RELEASE_CORE_IMAGE}:${tag}" >/dev/null
    docker pull "${RELEASE_AGENT_IMAGE}:${tag}" >/dev/null || true
  fi

  local cid
  cid="$(docker create "${RELEASE_CORE_IMAGE}:${tag}")"
  docker cp "${cid}:/opt/host-scripts/." "$tmpdir/"
  docker rm -f "$cid" >/dev/null

  if [[ -x "$tmpdir/scripts/install-host-scripts.sh" ]]; then
    YEDEK_ROOT="$tmpdir" bash "$tmpdir/scripts/install-host-scripts.sh"
  fi

  cd "$ROOT"
  compose -f docker-compose.yml -f docker-compose.release.yml up -d --force-recreate core
  if [[ -f /yedek/config/central-agent.env ]] && grep -q '^ORG_ENROLLMENT_CODE=' /yedek/config/central-agent.env; then
    compose --profile central -f docker-compose.yml -f docker-compose.release.yml up -d --force-recreate central-agent || true
  fi

  if ! curl -sf --max-time 8 http://127.0.0.1:8090/health >/dev/null; then
    echo "[$(ts)] ${phase}: health check failed tag=${tag}" >&2
    return 1
  fi

  if ! systemctl is-active --quiet yedek-backup-watcher.service; then
    echo "[$(ts)] ${phase}: backup watcher inactive tag=${tag}" >&2
    return 1
  fi

  return 0
}

if [[ -n "$RELEASE_READONLY_TOKEN" && "$RELEASE_SKIP_PULL" != "1" ]]; then
  echo "$RELEASE_READONLY_TOKEN" | docker login "$RELEASE_REGISTRY_HOST" -u "$RELEASE_REGISTRY_USER" --password-stdin >/dev/null 2>&1 || true
fi

PREV_TAG="$(read_current_tag)"
if [[ "$PREV_TAG" == "$RELEASE_TARGET_TAG" ]]; then
  write_state "ok" "Ayni release zaten calisiyor" "$PREV_TAG" "$RELEASE_TARGET_TAG"
  exit 0
fi

write_state "updating" "Release gecisi basladi" "$PREV_TAG" "$RELEASE_TARGET_TAG"
if deploy_tag "$RELEASE_TARGET_TAG" "deploy"; then
  write_state "ok" "Release guncellendi" "$RELEASE_TARGET_TAG" "$RELEASE_TARGET_TAG"
  echo "[$(ts)] release ok: ${RELEASE_TARGET_TAG}"
  exit 0
fi

if [[ -n "$PREV_TAG" ]]; then
  write_state "rollback" "Deploy hatasi, rollback deneniyor" "$PREV_TAG" "$RELEASE_TARGET_TAG"
  if deploy_tag "$PREV_TAG" "rollback"; then
    write_state "rolled_back" "Rollback basarili" "$PREV_TAG" "$RELEASE_TARGET_TAG"
    echo "[$(ts)] rollback ok: ${PREV_TAG}"
    exit 1
  fi
fi

write_state "failed" "Deploy/rollback basarisiz" "$PREV_TAG" "$RELEASE_TARGET_TAG"
exit 1
