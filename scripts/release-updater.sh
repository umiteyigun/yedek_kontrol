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

# Hub env ile gelen tag, source sonrasi kaybolmasin
_ENV_TARGET_TAG="${RELEASE_TARGET_TAG:-}"
_ENV_TRACK="${RELEASE_TRACK:-}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi

: "${RELEASE_UPDATER_ENABLED:=0}"
: "${RELEASE_TARGET_TAG:=}"
: "${RELEASE_TRACK:=pin}"

# Dosyadaki pin, hub/env ile gelen degeri ezmesin
if [[ -z "$FORCE_TAG" && -n "$_ENV_TARGET_TAG" ]]; then
  RELEASE_TARGET_TAG="$_ENV_TARGET_TAG"
fi
if [[ -z "$FORCE_TAG" && -n "$_ENV_TRACK" ]]; then
  RELEASE_TRACK="$_ENV_TRACK"
fi
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

# Python 2.6 / 2.7.5 (RHEL7): ssl.create_default_context ve urlopen(context=) yok.
ctx = None
if hasattr(ssl, "create_default_context"):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

def fetch(url, headers):
    req = Request(url, headers=headers)
    if ctx is not None:
        try:
            return urlopen(req, context=ctx).read()
        except TypeError:
            pass
    return urlopen(req).read()

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
if [[ -n "$FORCE_TAG" ]]; then
  # Hub/manuel: kilitli cron bitsin diye bekle
  if ! flock -w 600 9; then
    echo "[$(ts)] release-updater: lock timeout (FORCE_TAG=$FORCE_TAG)" >&2
    exit 1
  fi
else
  if ! flock -n 9; then
    exit 0
  fi
fi

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

compose_files() {
  local -a files=(-f "$ROOT/docker-compose.yml")
  if [[ -f "$COMPOSE_OVERRIDE" ]]; then
    files+=(-f "$COMPOSE_OVERRIDE")
  fi
  if [[ -f /yedek/config/docker-compose.volumes.yml ]]; then
    files+=(-f /yedek/config/docker-compose.volumes.yml)
  fi
  printf '%s\n' "${files[@]}"
}

COMPOSE_FP_FILE="/yedek/config/compose-applied.fp"
COMPOSE_RECREATE_FLAG="/yedek/config/compose-recreate.requested"

ensure_compose_base() {
  local base="$ROOT/docker-compose.yml"
  [[ -f "$base" ]] || return 0
  if grep -q '/yedek/orayedek:/yedek/orayedek' "$base" && ! grep -qE '/yedek:/yedek[^/]' "$base"; then
    sed -i 's|/yedek/orayedek:/yedek/orayedek|/yedek:/yedek|g' "$base"
    echo "[$(ts)] docker-compose.yml: /yedek tam mount'a guncellendi"
    return 0
  fi
  return 0
}

compose_fingerprint() {
  local tmp="" fp=""
  cd "$ROOT"
  mapfile -t _cf < <(compose_files)
  tmp="$(mktemp /tmp/yedek-compose-fp.XXXXXX)"
  if compose "${_cf[@]}" config >"$tmp" 2>/dev/null; then
    if command -v sha256sum >/dev/null 2>&1; then
      fp="$(sha256sum "$tmp" | awk '{print $1}')"
    else
      fp="$(shasum -a 256 "$tmp" | awk '{print $1}')"
    fi
  fi
  rm -f "$tmp"
  if [[ -f /yedek/config/backup-dirs.fp ]]; then
    local dirs_fp
    dirs_fp="$(tr -d '[:space:]' </yedek/config/backup-dirs.fp)"
    fp="${fp:-none}:${dirs_fp}"
  fi
  echo "${fp:-unknown}"
}

read_applied_compose_fp() {
  if [[ -f "$COMPOSE_FP_FILE" ]]; then
    tr -d '[:space:]' <"$COMPOSE_FP_FILE"
  fi
}

write_applied_compose_fp() {
  local fp="$1"
  printf '%s\n' "$fp" >"${COMPOSE_FP_FILE}.tmp"
  mv -f "${COMPOSE_FP_FILE}.tmp" "$COMPOSE_FP_FILE"
}

container_mounts_ok() {
  if ! docker inspect yedek-core >/dev/null 2>&1; then
    return 1
  fi
  if docker inspect yedek-core --format '{{range .Mounts}}{{println .Destination}}{{end}}' \
    | grep -qx '/yedek'; then
    return 0
  fi
  if [[ -f /yedek/config/backup-dirs.json ]]; then
    local py="" missing=0
    if command -v python3 >/dev/null 2>&1; then py=python3
    elif command -v python >/dev/null 2>&1; then py=python
    else return 1
    fi
    missing="$("$py" - <<'PY'
import json, subprocess, sys
try:
    data = json.load(open("/yedek/config/backup-dirs.json"))
except (IOError, ValueError):
    sys.exit(1)
dests = set(subprocess.check_output(
    ["docker", "inspect", "yedek-core", "--format", "{{range .Mounts}}{{println .Destination}}{{end}}"],
).decode().split())
dirs = data.get("unique_dirs") or []
for raw in dirs:
    path = str(raw).rstrip("/") or "/yedek"
    if path not in dests and not any(d == "/yedek" for d in dests):
        sys.exit(2)
sys.exit(0)
PY
)"
    [[ "$missing" -eq 0 ]]
    return
  fi
  docker inspect yedek-core --format '{{range .Mounts}}{{println .Destination}}{{end}}' \
    | grep -qx '/yedek/orayedek'
}

needs_compose_recreate() {
  local desired applied
  desired="$(compose_fingerprint)"
  applied="$(read_applied_compose_fp)"
  if [[ -f "$COMPOSE_RECREATE_FLAG" ]]; then
    echo "[$(ts)] compose-recreate.requested bayragi aktif"
    return 0
  fi
  if [[ -n "$applied" && "$applied" != "$desired" ]]; then
    echo "[$(ts)] compose fingerprint degisti (applied=${applied:0:12}.. desired=${desired:0:12}..)"
    return 0
  fi
  if ! container_mounts_ok; then
    echo "[$(ts)] yedek-core mount'lari beklenen yedek dizinlerini kapsamiyor"
    return 0
  fi
  return 1
}

recreate_current_tag() {
  local tag="$1"
  write_state "updating" "Compose/mount degisikligi — container yeniden olusturuluyor" "$tag" "$tag"
  if deploy_tag "$tag" "recreate"; then
    write_applied_compose_fp "$(compose_fingerprint)"
    rm -f "$COMPOSE_RECREATE_FLAG"
    write_state "ok" "Compose recreate tamamlandi (tag=${tag})" "$tag" "$tag"
    echo "[$(ts)] compose recreate ok: tag=${tag}"
    return 0
  fi
  write_state "failed" "Compose recreate basarisiz (tag=${tag})" "$tag" "$tag"
  return 1
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
  mapfile -t COMPOSE_FILES < <(compose_files)
  compose "${COMPOSE_FILES[@]}" up -d --force-recreate core
  if [[ -f /yedek/config/central-agent.env ]] && grep -q '^ORG_ENROLLMENT_CODE=' /yedek/config/central-agent.env; then
    compose --profile central "${COMPOSE_FILES[@]}" up -d --force-recreate central-agent || true
  fi

  if ! curl -sf --max-time 8 http://127.0.0.1:8090/health >/dev/null; then
    echo "[$(ts)] ${phase}: health check failed tag=${tag}" >&2
    return 1
  fi

  if ! systemctl is-active --quiet yedek-backup-watcher.service; then
    echo "[$(ts)] ${phase}: backup watcher inactive tag=${tag}" >&2
    return 1
  fi

  write_applied_compose_fp "$(compose_fingerprint)"
  rm -f "$COMPOSE_RECREATE_FLAG"

  return 0
}

if [[ -n "$RELEASE_READONLY_TOKEN" && "$RELEASE_SKIP_PULL" != "1" ]]; then
  echo "$RELEASE_READONLY_TOKEN" | docker login "$RELEASE_REGISTRY_HOST" -u "$RELEASE_REGISTRY_USER" --password-stdin >/dev/null 2>&1 || true
fi

ensure_compose_base

PREV_TAG="$(read_current_tag)"
RUNNING_TAG=""
if docker inspect yedek-core >/dev/null 2>&1; then
  RUNNING_TAG="$(docker inspect yedek-core --format '{{.Config.Image}}' | awk -F: '{print $NF}')"
fi
if [[ -z "$PREV_TAG" && -n "$RUNNING_TAG" ]]; then
  PREV_TAG="$RUNNING_TAG"
fi

if [[ "$PREV_TAG" == "$TARGET_TAG" ]]; then
  if needs_compose_recreate; then
    recreate_current_tag "$TARGET_TAG" || exit 1
    exit 0
  fi
  write_state "ok" "Ayni release zaten calisiyor" "$PREV_TAG" "$TARGET_TAG"
  exit 0
fi

write_state "updating" "Release gecisi basladi" "$PREV_TAG" "$TARGET_TAG"
if deploy_tag "$TARGET_TAG" "deploy"; then
  write_state "ok" "Release guncellendi" "$TARGET_TAG" "$TARGET_TAG"
  # Hub/manuel --tag sonrasi pin kilidini kaldir; sonraki cron latest takip etsin
  if [[ -n "$FORCE_TAG" && "${RELEASE_UNLOCK_LATEST:-1}" == "1" && -f "$ENV_FILE" ]]; then
    if grep -q "^RELEASE_TRACK=" "$ENV_FILE" 2>/dev/null; then
      sed -i "s/^RELEASE_TRACK=.*/RELEASE_TRACK=latest/" "$ENV_FILE"
    else
      echo "RELEASE_TRACK=latest" >>"$ENV_FILE"
    fi
    if grep -q "^RELEASE_TARGET_TAG=" "$ENV_FILE" 2>/dev/null; then
      sed -i "s/^RELEASE_TARGET_TAG=.*/RELEASE_TARGET_TAG=${TARGET_TAG}/" "$ENV_FILE"
    else
      echo "RELEASE_TARGET_TAG=${TARGET_TAG}" >>"$ENV_FILE"
    fi
    echo "[$(ts)] unlock: RELEASE_TRACK=latest (fallback pin=${TARGET_TAG})"
  fi
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
