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

# Manuel override: release-updater.sh --tag 16  (veya ilk arguman)
FORCE_TAG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      FORCE_TAG="${2:-}"
      shift 2
      ;;
    --tag=*)
      FORCE_TAG="${1#--tag=}"
      shift
      ;;
    -*)
      echo "Kullanim: $0 [--tag TAG]" >&2
      exit 2
      ;;
    *)
      FORCE_TAG="$1"
      shift
      ;;
  esac
done

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi

: "${RELEASE_UPDATER_ENABLED:=0}"
: "${RELEASE_TARGET_TAG:=}"
: "${RELEASE_TRACK:=pin}"
: "${RELEASE_MANIFEST_URL:=https://git.trtek.tr/yedek_kontrol_public/-/raw/main/release/latest.env}"
: "${RELEASE_CORE_IMAGE:=git.trtek.tr/umiteyigun/yedek_kontrol/yedek-core}"
: "${RELEASE_AGENT_IMAGE:=git.trtek.tr/umiteyigun/yedek_kontrol/yedek-central-agent}"
: "${RELEASE_REGISTRY_HOST:=git.trtek.tr}"
: "${RELEASE_READONLY_TOKEN:=}"
: "${RELEASE_REGISTRY_USER:=oauth2}"
: "${RELEASE_SKIP_PULL:=0}"

# Hub/manuel tetik: sabit tag zorla (latest track'i gecici kapat)
if [[ -n "$FORCE_TAG" ]]; then
  RELEASE_TARGET_TAG="$FORCE_TAG"
  RELEASE_TRACK=pin
  RELEASE_UPDATER_ENABLED=1
fi

resolve_target_tag_from_registry() {
  local py=""
  if command -v python3 >/dev/null 2>&1; then
    py=python3
  elif command -v python >/dev/null 2>&1; then
    py=python
  else
    return 1
  fi
  "$py" - "$RELEASE_REGISTRY_HOST" "$RELEASE_CORE_IMAGE" "$RELEASE_REGISTRY_USER" "$RELEASE_READONLY_TOKEN" <<'PY'
import base64
import json
import os
import ssl
import sys

try:
    from urllib.request import Request, urlopen
except ImportError:
    import urllib2 as _urllib2
    Request = _urllib2.Request
    urlopen = _urllib2.urlopen

registry, image, user, token = sys.argv[1:5]
if not registry or not image or not token:
    sys.exit(1)
repo = image.split(registry + "/", 1)[-1] if registry + "/" in image else image
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

def fetch(url, headers):
    req = Request(url, headers=headers)
    return urlopen(req, context=ctx).read()

auth = base64.b64encode((user + ":" + token).encode("utf-8")).decode("ascii")
token_url = "https://%s/v2/token?service=%s&scope=repository:%s:pull" % (registry, registry, repo)
token_payload = json.loads(fetch(token_url, {"Authorization": "Basic " + auth}))
bearer = token_payload.get("token") or ""
if not bearer:
    sys.exit(1)
tags_url = "https://%s/v2/%s/tags/list" % (registry, repo)
tags_payload = json.loads(fetch(tags_url, {"Authorization": "Bearer " + bearer}))
tags = [int(t) for t in tags_payload.get("tags", []) if str(t).isdigit()]
if not tags:
    sys.exit(1)
print(max(tags))
PY
}

resolve_target_tag() {
  local track="${RELEASE_TRACK:-pin}"
  local pinned="${RELEASE_TARGET_TAG:-}"
  if [[ "$track" == "latest" ]]; then
    local url="${RELEASE_MANIFEST_URL:-}"
    local tmp manifest_tag="" registry_tag=""
    if [[ -n "$url" ]]; then
      tmp="$(mktemp /tmp/yedek-release-manifest.XXXXXX)"
      if curl -skf --max-time 20 "$url" -o "$tmp"; then
        manifest_tag="$(grep -m1 '^RELEASE_TARGET_TAG=' "$tmp" | cut -d= -f2- | tr -d '[:space:]')"
        if [[ -n "$manifest_tag" ]]; then
          rm -f "$tmp"
          echo "$manifest_tag"
          return 0
        fi
      fi
      rm -f "$tmp"
    fi
    if registry_tag="$(resolve_target_tag_from_registry)"; then
      echo "$registry_tag"
      return 0
    fi
    echo "[$(ts)] latest tag cozulemedi, sabit tag kullaniliyor: ${pinned:-yok}" >&2
  fi
  echo "$pinned"
}

[[ "$RELEASE_UPDATER_ENABLED" == "1" ]] || exit 0
TARGET_TAG="$(resolve_target_tag)"
[[ -n "$TARGET_TAG" ]] || exit 0
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
if [[ "$PREV_TAG" == "$TARGET_TAG" ]]; then
  write_state "ok" "Ayni release zaten calisiyor" "$PREV_TAG" "$TARGET_TAG"
  exit 0
fi

write_state "updating" "Release gecisi basladi" "$PREV_TAG" "$TARGET_TAG"
if deploy_tag "$TARGET_TAG" "deploy"; then
  write_state "ok" "Release guncellendi" "$TARGET_TAG" "$TARGET_TAG"
  echo "[$(ts)] release ok: ${TARGET_TAG}"
  exit 0
fi

if [[ -n "$PREV_TAG" ]]; then
  write_state "rollback" "Deploy hatasi, rollback deneniyor" "$PREV_TAG" "$TARGET_TAG"
  if deploy_tag "$PREV_TAG" "rollback"; then
    write_state "rolled_back" "Rollback basarili" "$PREV_TAG" "$TARGET_TAG"
    echo "[$(ts)] rollback ok: ${PREV_TAG}"
    exit 1
  fi
fi

write_state "failed" "Deploy/rollback basarisiz" "$PREV_TAG" "$TARGET_TAG"
exit 1
