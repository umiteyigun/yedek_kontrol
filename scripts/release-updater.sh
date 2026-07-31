#!/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PATH
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
: "${RELEASE_MANIFEST_URL:=https://centos.trtekyazilim.com:8444/release/latest.env}"
: "${HUB_MANIFEST_URL:=https://centos.trtekyazilim.com:8444/release/latest.env}"
: "${RELEASE_CORE_IMAGE:=git.trtek.tr/umiteyigun/yedek_kontrol/yedek-core}"
: "${RELEASE_AGENT_IMAGE:=git.trtek.tr/umiteyigun/yedek_kontrol/yedek-central-agent}"
: "${RELEASE_REGISTRY_HOST:=git.trtek.tr}"
: "${RELEASE_READONLY_TOKEN:=}"
: "${RELEASE_REGISTRY_USER:=oauth2}"
: "${RELEASE_SKIP_PULL:=0}"
# compose up sonrasi core hemen hazir olmayabilir — health retry
: "${RELEASE_HEALTH_URL:=http://127.0.0.1:18090/health}"
: "${RELEASE_HEALTH_RETRIES:=40}"
: "${RELEASE_HEALTH_INTERVAL:=3}"
: "${RELEASE_HEALTH_CURL_MAX:=5}"
: "${RELEASE_FAIL_MAX:=3}"
: "${RELEASE_FAIL_COOLDOWN_SEC:=1800}"
FAIL_STATE_FILE="/yedek/config/release-fail.state"
UPD_CANON="/yedek/config/release-updater.sh"
UPD_OPT="/opt/yedek_kontrol/scripts/release-updater.sh"

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

# HTTP manifesto indirme: OneDev login HTML / DOCTYPE ele; gecerli tag dondur
fetch_manifest_tag() {
  local url="$1"
  local tmp manifest_tag=""
  [[ -n "$url" ]] || return 1
  tmp="$(mktemp /tmp/yedek-release-manifest.XXXXXX)"
  if ! curl -skf --max-time 20 "$url" -o "$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  if grep -qiE '<!doctype|<html|/~login' "$tmp" 2>/dev/null; then
    echo "[$(ts)] manifest HTML/login (atlanıyor): $url" >&2
    rm -f "$tmp"
    return 1
  fi
  manifest_tag="$(grep -m1 '^RELEASE_TARGET_TAG=' "$tmp" | cut -d= -f2- | tr -d '[:space:]')"
  rm -f "$tmp"
  if [[ -n "$manifest_tag" && "$manifest_tag" =~ ^[0-9A-Za-z._-]+$ ]]; then
    echo "$manifest_tag"
    return 0
  fi
  return 1
}

resolve_target_tag() {
  local track="${RELEASE_TRACK:-pin}"
  local pinned="${RELEASE_TARGET_TAG:-}"
  if [[ "$track" == "latest" ]]; then
    local url="" manifest_tag="" registry_tag=""
    local -a urls=()
    # 1) env manifest  2) hub public  (ayni URL tekrarlanmasin)
    [[ -n "${RELEASE_MANIFEST_URL:-}" ]] && urls+=("${RELEASE_MANIFEST_URL}")
    [[ -n "${HUB_MANIFEST_URL:-}" && "${HUB_MANIFEST_URL}" != "${RELEASE_MANIFEST_URL:-}" ]] && urls+=("${HUB_MANIFEST_URL}")
    for url in "${urls[@]}"; do
      if manifest_tag="$(fetch_manifest_tag "$url")"; then
        echo "[$(ts)] manifest OK ($url) -> $manifest_tag" >&2
        # Offline fallback icin pin'i Hub latest ile hizala (registry max'a ziplama)
        if [[ -f "$ENV_FILE" ]]; then
          if grep -q '^RELEASE_TARGET_TAG=' "$ENV_FILE" 2>/dev/null; then
            sed -i "s|^RELEASE_TARGET_TAG=.*|RELEASE_TARGET_TAG=${manifest_tag}|" "$ENV_FILE"
          else
            echo "RELEASE_TARGET_TAG=${manifest_tag}" >>"$ENV_FILE"
          fi
        fi
        echo "$manifest_tag"
        return 0
      fi
    done
    # Hub :8444 cogu hastanede kapali — latest icin centos/registry kaynagi
    # (cleanup artik guvenli; calisan yedek-core silinmez)
    if registry_tag="$(resolve_target_tag_from_registry)"; then
      echo "[$(ts)] registry OK -> ${registry_tag} (hub manifesto yok/erisilemez)" >&2
      if [[ -f "$ENV_FILE" ]]; then
        if grep -q '^RELEASE_TARGET_TAG=' "$ENV_FILE" 2>/dev/null; then
          sed -i "s|^RELEASE_TARGET_TAG=.*|RELEASE_TARGET_TAG=${registry_tag}|" "$ENV_FILE"
        else
          echo "RELEASE_TARGET_TAG=${registry_tag}" >>"$ENV_FILE"
        fi
      fi
      echo "$registry_tag"
      return 0
    fi
    echo "[$(ts)] latest tag cozulemedi, sabit tag kullaniliyor: ${pinned:-yok}" >&2
  fi
  echo "$pinned"
}

is_numeric_tag() {
  [[ "${1:-}" =~ ^[0-9]+$ ]]
}

# a > b (ikisi de numeric); degilse 1
tag_gt() {
  local a="${1:-}" b="${2:-}"
  is_numeric_tag "$a" && is_numeric_tag "$b" || return 1
  (( 10#$a > 10#$b ))
}

# Fallback pin = latest cozulemezse tutunacagimiz tag. Calisan/hedef ile hizala.
sync_fallback_pin() {
  local tag="${1:-}"
  [[ -n "$tag" && -f "$ENV_FILE" ]] || return 0
  local current
  current="$(grep -m1 '^RELEASE_TARGET_TAG=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]')"
  if [[ "$current" == "$tag" ]]; then
    return 0
  fi
  if grep -q '^RELEASE_TARGET_TAG=' "$ENV_FILE" 2>/dev/null; then
    sed -i "s/^RELEASE_TARGET_TAG=.*/RELEASE_TARGET_TAG=${tag}/" "$ENV_FILE"
  else
    echo "RELEASE_TARGET_TAG=${tag}" >>"$ENV_FILE"
  fi
  echo "[$(ts)] fallback pin guncellendi: ${current:-yok} -> ${tag}"
}

unlock_track_latest() {
  [[ -f "$ENV_FILE" ]] || return 0
  if grep -q '^RELEASE_TRACK=' "$ENV_FILE" 2>/dev/null; then
    sed -i 's/^RELEASE_TRACK=.*/RELEASE_TRACK=latest/' "$ENV_FILE"
  else
    echo 'RELEASE_TRACK=latest' >>"$ENV_FILE"
  fi
}

# Chicken-egg kirici: Hub overlay'den guncel updater al, bir kez self-exec.
bootstrap_updater_from_hub() {
  [[ "${YEDEK_UPDATER_BOOTSTRAPPED:-0}" == "1" ]] && return 0
  local hub_base overlay tmp marker self
  hub_base="${RELEASE_MANIFEST_URL%/latest.env}"
  hub_base="${hub_base:-https://centos.trtekyazilim.com:8444/release}"
  overlay="${hub_base}/release-updater.sh"
  tmp="$(mktemp /tmp/ru.hub.XXXXXX 2>/dev/null || echo /tmp/ru.hub.$$)"
  if ! curl -skf --connect-timeout 5 --max-time 25 "$overlay" -o "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    return 0
  fi
  if ! grep -q 'ASLA silme' "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    return 0
  fi
  if ! grep -q 'wait_for_core_health\|bootstrap_updater_from_hub\|release-fail.state' "$tmp" 2>/dev/null; then
    # Cok eski/yanlis overlay — uygulama
    rm -f "$tmp"
    return 0
  fi
  install -m 755 "$tmp" "$UPD_CANON" 2>/dev/null || true
  install -m 755 "$tmp" "$UPD_OPT" 2>/dev/null || true
  rm -f "$tmp"
  self="$UPD_CANON"
  [[ -x "$self" ]] || self="$0"
  echo "[$(ts)] bootstrap: hub overlay applied — re-exec" >&2
  export YEDEK_UPDATER_BOOTSTRAPPED=1
  if [[ -n "$FORCE_TAG" ]]; then
    exec bash "$self" --tag "$FORCE_TAG"
  else
    exec bash "$self"
  fi
}

clear_fail_state() {
  rm -f "$FAIL_STATE_FILE" 2>/dev/null || true
}

read_fail_count() {
  local n=0
  if [[ -f "$FAIL_STATE_FILE" ]]; then
    n="$(sed -n 's/.*"fail_count":\([0-9]*\).*/\1/p' "$FAIL_STATE_FILE" | head -1)"
  fi
  [[ "$n" =~ ^[0-9]+$ ]] || n=0
  echo "$n"
}

cooldown_active() {
  [[ -f "$FAIL_STATE_FILE" ]] || return 1
  local until_ts now
  until_ts="$(sed -n 's/.*"cooldown_until":\([0-9]*\).*/\1/p' "$FAIL_STATE_FILE" | head -1)"
  [[ "$until_ts" =~ ^[0-9]+$ ]] || return 1
  now="$(date +%s)"
  (( now < until_ts ))
}

arm_fail_cooldown() {
  local running="${1:-}"
  local count until_ts now
  now="$(date +%s)"
  count="$(read_fail_count)"
  count=$((count + 1))
  until_ts=0
  if (( count >= RELEASE_FAIL_MAX )); then
    until_ts=$((now + RELEASE_FAIL_COOLDOWN_SEC))
    echo "[$(ts)] cooldown armed: fail_count=${count} until=$(date -d "@${until_ts}" '+%Y-%m-%d %H:%M:%S' 2>/dev/null || echo "$until_ts") sec=${RELEASE_FAIL_COOLDOWN_SEC}" >&2
    if [[ -n "$running" ]]; then
      sync_fallback_pin "$running"
    fi
  else
    echo "[$(ts)] fail streak ${count}/${RELEASE_FAIL_MAX} (cooldown yok)" >&2
  fi
  mkdir -p "$(dirname "$FAIL_STATE_FILE")"
  cat >"${FAIL_STATE_FILE}.tmp" <<EOF
{"fail_count":${count},"cooldown_until":${until_ts},"updated_at":$(date +%s),"running_tag":"${running}"}
EOF
  mv -f "${FAIL_STATE_FILE}.tmp" "$FAIL_STATE_FILE"
}

running_core_tag() {
  if docker inspect yedek-core >/dev/null 2>&1; then
    docker inspect yedek-core --format '{{.Config.Image}}' 2>/dev/null | awk -F: '{print $NF}'
  else
    echo ""
  fi
}

[[ "$RELEASE_UPDATER_ENABLED" == "1" ]] || exit 0
[[ -d "$ROOT" ]] || exit 1

# Hub'dan guncel updater (eski hostlar thrash'te kalmasin)
bootstrap_updater_from_hub

# Kilit ONCE alinir. Cron'da `flock LOCK release-updater.sh` varsa child flock -n
# basarisiz olur (cift kilit) — parent flock bizi zaten tek runner yapar, devam et.
#
# Bilinen bug: backup-watcher nohup ile baslatilirken fd 9 (flock) miras alirsa
# updater biter, watcher kilidi sonsuza tutar → cron/timer surekli "lock busy".
reclaim_foreign_release_lock() {
  local reason="${1:-foreign}"
  local pids pid cmd
  local leak_pids=""

  # ONCE kilit tutanlara bak. bash -c ile spawn edilen force updater da gercektir —
  # pgrep'de ignore edip fuser -k yapmak ortadaki compose'u oldurup cron cakismasi yaratir.
  if [[ -e "$LOCK_FILE" ]]; then
    pids="$(fuser "$LOCK_FILE" 2>/dev/null | tr -cs '0-9' ' ' || true)"
    for pid in $pids; do
      [[ -z "$pid" || "$pid" == "$$" ]] && continue
      cmd="$(ps -o args= -p "$pid" 2>/dev/null || true)"
      [[ -z "$cmd" ]] && continue
      if [[ "$cmd" == *release-updater.sh* ]]; then
        echo "[$(ts)] release-updater: $reason skip reclaim (lock held by updater pid=$pid)" >&2
        return 1
      fi
      # Leak: backup-watcher / sleep flock mirasi
      if [[ "$cmd" == *backup-watcher* || "$cmd" == *" sleep "* || "$cmd" == "sleep "* ]]; then
        leak_pids="${leak_pids} ${pid}"
      else
        echo "[$(ts)] release-updater: $reason skip reclaim (unknown lock holder pid=$pid cmd=${cmd:0:80})" >&2
        return 1
      fi
    done
  fi

  # Dogrudan (bash -c degil) baska updater calisiyor mu?
  for pid in $(pgrep -f "release-updater\\.sh" 2>/dev/null || true); do
    [[ -z "$pid" || "$pid" == "$$" ]] && continue
    cmd="$(ps -o args= -p "$pid" 2>/dev/null || true)"
    [[ -z "$cmd" ]] && continue
    # Hub bootstrap wrapper (henuz flock almadan): bash -lc '... release-updater.sh ...'
    if [[ "$cmd" == *"bash -lc"* || "$cmd" == *"bash -c"* ]]; then
      continue
    fi
    if [[ "$cmd" == *release-updater.sh* ]]; then
      echo "[$(ts)] release-updater: $reason skip reclaim (other updater running pid=$pid)" >&2
      return 1
    fi
  done

  if [[ ! -e "$LOCK_FILE" ]]; then
    return 0
  fi

  if [[ -n "${leak_pids// /}" ]]; then
    echo "[$(ts)] release-updater: $reason reclaim leak holders pids=${leak_pids}" >&2
    for pid in $leak_pids; do
      kill "$pid" >/dev/null 2>&1 || true
    done
    sleep 1
  fi
  rm -f "$LOCK_FILE" 2>/dev/null || true
  if [[ -x /yedek/config/backup-watcher.sh ]] && ! pgrep -f '/yedek/config/backup-watcher\.sh' >/dev/null 2>&1; then
    mkdir -p /yedek/orayedek
    nohup /yedek/config/backup-watcher.sh >>/yedek/orayedek/backup-watcher.log 2>&1 9>&- &
    echo "[$(ts)] release-updater: backup-watcher restarted after lock reclaim" >&2
  fi
  return 0
}

reclaim_foreign_release_lock "precheck" || true

exec 9>"$LOCK_FILE"
if [[ -n "$FORCE_TAG" ]]; then
  if ! flock -w 120 9; then
    echo "[$(ts)] release-updater: lock busy — force reclaim (FORCE_TAG=$FORCE_TAG)" >&2
    if ! reclaim_foreign_release_lock "force"; then
      echo "[$(ts)] release-updater: lock timeout (other updater holds lock, FORCE_TAG=$FORCE_TAG)" >&2
      exit 1
    fi
    sleep 1
    exec 9>"$LOCK_FILE"
    if ! flock -w 30 9; then
      echo "[$(ts)] release-updater: lock timeout (FORCE_TAG=$FORCE_TAG)" >&2
      exit 1
    fi
  fi
elif [[ -n "${YEDEK_RELEASE_LOCK_HELD:-}" ]]; then
  : # cron: flock ... env YEDEK_RELEASE_LOCK_HELD=1 release-updater.sh
elif ! flock -n 9; then
  ppcmd="$(ps -o args= -p "${PPID:-0}" 2>/dev/null || true)"
  if [[ "$ppcmd" == *flock* && ( "$ppcmd" == *yedek-release-update* || "$ppcmd" == *release-updater* ) ]]; then
    echo "[$(ts)] release-updater: lock held by parent flock; continuing" >&2
  else
    # Gercek updater yoksa (sadece backup-watcher vb.) kilidi al, bir kez daha dene
    if reclaim_foreign_release_lock "busy"; then
      exec 9>"$LOCK_FILE"
      if flock -n 9; then
        echo "[$(ts)] release-updater: lock reclaimed after foreign holder" >&2
      else
        echo "[$(ts)] release-updater: skip (lock busy)" >&2
        exit 0
      fi
    else
      echo "[$(ts)] release-updater: skip (lock busy)" >&2
      exit 0
    fi
  fi
fi

# Fail cooldown: thrash kes (FORCE_TAG her zaman gecer)
if [[ -z "$FORCE_TAG" ]] && cooldown_active; then
  local_until="$(sed -n 's/.*"cooldown_until":\([0-9]*\).*/\1/p' "$FAIL_STATE_FILE" | head -1)"
  echo "[$(ts)] skip: fail cooldown active until=${local_until} (FORCE_TAG ile gecilebilir)" >&2
  exit 0
fi

TARGET_TAG="$(resolve_target_tag)"
[[ -n "$TARGET_TAG" ]] || exit 0
# Deploy boyunca cron yazilmasin / ateslenmesin (ensure-release-host + cron.d race)
export YEDEK_RELEASE_DEPLOYING=1
if [[ -f /etc/cron.d/yedek-release-update ]]; then
  mv -f /etc/cron.d/yedek-release-update /var/tmp/yedek-release-update.cron.paused 2>/dev/null || rm -f /etc/cron.d/yedek-release-update
fi
rm -f /etc/cron.d/yedek-release-update.paused 2>/dev/null || true
systemctl disable --now yedek-release-update.timer 2>/dev/null || true
restore_release_cron() {
  unset YEDEK_RELEASE_DEPLOYING
  export YEDEK_RELEASE_ALLOW_CRON=1
  if [[ -x /yedek/config/ensure-release-host.sh ]]; then
    /yedek/config/ensure-release-host.sh >/dev/null 2>&1 || true
  elif [[ -f /var/tmp/yedek-release-update.cron.paused ]]; then
    mv -f /var/tmp/yedek-release-update.cron.paused /etc/cron.d/yedek-release-update 2>/dev/null || true
  fi
}
trap restore_release_cron EXIT
echo "[$(ts)] target=${TARGET_TAG} track=${RELEASE_TRACK:-pin} force=${FORCE_TAG:-}"

# Docker CLI: flock fd mirasini kes
_docker() { docker "$@" 9>&-; }

compose() {
  # cron PATH dar olabilir (/usr/local/bin eksik) — absolute fallback
  # 9>&- : flock fd docker compose'a miras etmesin
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@" 9>&-
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@" 9>&-
  elif [[ -x /usr/local/bin/docker-compose ]]; then
    /usr/local/bin/docker-compose "$@" 9>&-
  elif [[ -x /usr/libexec/docker/cli-plugins/docker-compose ]]; then
    /usr/libexec/docker/cli-plugins/docker-compose "$@" 9>&-
  else
    echo "[$(ts)] docker compose / docker-compose bulunamadi (PATH=$PATH)" >&2
    return 127
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
  if [[ -f /yedek/config/docker-compose.host-exec.yml ]]; then
    files+=(-f /yedek/config/docker-compose.host-exec.yml)
  fi
  printf '%s\n' "${files[@]}"
}

COMPOSE_FP_FILE="/yedek/config/compose-applied.fp"
COMPOSE_RECREATE_FLAG="/yedek/config/compose-recreate.requested"
HOST_EXEC_COMPOSE="/yedek/config/docker-compose.host-exec.yml"

ensure_compose_base() {
  local base="$ROOT/docker-compose.yml"
  [[ -f "$base" ]] || return 0
  if grep -q '/yedek/orayedek:/yedek/orayedek' "$base" && ! grep -qE '/yedek:/yedek[^/]' "$base"; then
    sed -i 's|/yedek/orayedek:/yedek/orayedek|/yedek:/yedek|g' "$base"
    echo "[$(ts)] docker-compose.yml: /yedek tam mount'a guncellendi"
  fi
  # Panel-down kurtarma: agent docker.sock + host config (host_exec)
  cat >"${HOST_EXEC_COMPOSE}.tmp" <<'YAML'
services:
  central-agent:
    privileged: true
    pid: host
    volumes:
      - /yedek/config/agent-state:/var/lib/yedek-agent
      - /var/run/docker.sock:/var/run/docker.sock
      - /yedek/config:/yedek/config
      - /opt/yedek_kontrol:/opt/yedek_kontrol:ro
YAML
  if ! cmp -s "${HOST_EXEC_COMPOSE}.tmp" "$HOST_EXEC_COMPOSE" 2>/dev/null; then
    mv -f "${HOST_EXEC_COMPOSE}.tmp" "$HOST_EXEC_COMPOSE"
    echo "[$(ts)] docker-compose.host-exec.yml: agent host_exec mount yazildi"
    touch "$COMPOSE_RECREATE_FLAG"
  else
    rm -f "${HOST_EXEC_COMPOSE}.tmp"
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
    clear_fail_state
    echo "[$(ts)] compose recreate ok: tag=${tag}"
    return 0
  fi
  write_state "failed" "Compose recreate basarisiz (tag=${tag})" "$tag" "$tag"
  arm_fail_cooldown "$tag"
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
  trap 'rm -rf "$tmpdir"; trap - RETURN' RETURN

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
    if ! _docker pull "${RELEASE_CORE_IMAGE}:${tag}" >/dev/null; then
      echo "[$(ts)] ${phase}: core image pull failed tag=${tag}" >&2
      return 1
    fi
    if [[ -f /yedek/config/central-agent.env ]] && grep -q '^ORG_ENROLLMENT_CODE=' /yedek/config/central-agent.env; then
      if ! _docker pull "${RELEASE_AGENT_IMAGE}:${tag}" >/dev/null; then
        echo "[$(ts)] ${phase}: central-agent image pull failed tag=${tag}" >&2
        return 1
      fi
    fi
  fi

  local cid
  if ! cid="$(_docker create "${RELEASE_CORE_IMAGE}:${tag}")"; then
    echo "[$(ts)] ${phase}: core helper container create failed tag=${tag}" >&2
    return 1
  fi
  if ! _docker cp "${cid}:/opt/host-scripts/." "$tmpdir/"; then
    _docker rm -f "$cid" >/dev/null 2>&1 || true
    echo "[$(ts)] ${phase}: host scripts extract failed tag=${tag}" >&2
    return 1
  fi
  _docker rm -f "$cid" >/dev/null || return 1

  if [[ -x "$tmpdir/scripts/install-host-scripts.sh" ]]; then
    if ! YEDEK_ROOT="$tmpdir" bash "$tmpdir/scripts/install-host-scripts.sh"; then
      echo "[$(ts)] ${phase}: host scripts install failed tag=${tag}" >&2
      return 1
    fi
  fi

  # Ara tag image scriptleri bozuk olabiliyor; Hub'daki kanonik updater'i ustune yaz
  # (calisan process eski fonksiyonlari tutar; sonraki cron dogru scriptle baslar)
  _hub_base="${RELEASE_MANIFEST_URL%/latest.env}"
  _hub_base="${_hub_base:-https://centos.trtekyazilim.com:8444/release}"
  if curl -skf --connect-timeout 5 --max-time 20 "${_hub_base}/release-updater.sh" -o /tmp/ru.hub.$$ 2>/dev/null; then
    if grep -q 'ASLA silme' /tmp/ru.hub.$$ 2>/dev/null; then
      install -m 755 /tmp/ru.hub.$$ /yedek/config/release-updater.sh
      install -m 755 /tmp/ru.hub.$$ /opt/yedek_kontrol/scripts/release-updater.sh 2>/dev/null || true
      echo "[$(ts)] ${phase}: hub overlay release-updater applied" >&2
    fi
    rm -f /tmp/ru.hub.$$
  fi

  # Image'dan gelen bozuk cleanup'i ag olmadan da duzelt (calisan yedek-core silinmesin)
  sanitize_release_updater_cleanup() {
    local f
    for f in /yedek/config/release-updater.sh /opt/yedek_kontrol/scripts/release-updater.sh; do
      [[ -f "$f" ]] || continue
      if grep -q '== \*yedek-core\* ||' "$f" 2>/dev/null; then
        sed -i 's/== \*yedek-core\* || "\$name" == \*yedek-central-agent\*/== *_yedek-core || "$name" == *_yedek-central-agent/' "$f" || true
        echo "[$(ts)] ${phase}: sanitized dangerous cleanup in $f" >&2
      fi
    done
  }
  sanitize_release_updater_cleanup

  cd "$ROOT"
  mapfile -t COMPOSE_FILES < <(compose_files)

  # Yarim kalan recreate / isim cakismasi (baska updater oldurulurse kalir)
  cleanup_stale_core_names() {
    local id name
    while read -r id name; do
      [[ -z "$id" ]] && continue
      # Sadece compose conflict isimleri (hash_yedek-core); calisan yedek-core'u ASLA silme
      if [[ "$name" == *_yedek-core || "$name" == *_yedek-central-agent ]]; then
        echo "[$(ts)] ${phase}: removing stale container name=$name id=$id" >&2
        _docker rm -f "$id" >/dev/null 2>&1 || true
      fi
    done < <(_docker ps -a --format '{{.ID}} {{.Names}}' 2>/dev/null | grep -E 'yedek-core|yedek-central-agent' || true)
  }

  compose_up_retry() {
    local svc="$1"
    # set -u: bos array "${profile[@]}" unbound — profili ayri cagir
    if [[ "$svc" == "central-agent" ]]; then
      if compose --profile central "${COMPOSE_FILES[@]}" up -d --force-recreate "$svc"; then
        return 0
      fi
      echo "[$(ts)] ${phase}: compose conflict/retry for $svc" >&2
      cleanup_stale_core_names
      compose --profile central "${COMPOSE_FILES[@]}" up -d --force-recreate "$svc"
      return $?
    fi
    if compose "${COMPOSE_FILES[@]}" up -d --force-recreate "$svc"; then
      return 0
    fi
    echo "[$(ts)] ${phase}: compose conflict/retry for $svc" >&2
    cleanup_stale_core_names
    compose "${COMPOSE_FILES[@]}" up -d --force-recreate "$svc"
  }

  cleanup_stale_core_names
  if ! compose_up_retry core; then
    echo "[$(ts)] ${phase}: core compose failed tag=${tag}" >&2
    return 1
  fi
  if [[ -f /yedek/config/central-agent.env ]] && grep -q '^ORG_ENROLLMENT_CODE=' /yedek/config/central-agent.env; then
    if ! compose_up_retry central-agent; then
      echo "[$(ts)] ${phase}: central-agent compose failed tag=${tag}" >&2
      return 1
    fi
  fi

  # Core uvicorn/nginx gec acilabiliyor; tek seferlik curl false-negative → thrash.
  wait_for_core_health() {
    local attempt=1
    local max="${RELEASE_HEALTH_RETRIES:-40}"
    local interval="${RELEASE_HEALTH_INTERVAL:-3}"
    local url="${RELEASE_HEALTH_URL:-http://127.0.0.1:18090/health}"
    local curl_max="${RELEASE_HEALTH_CURL_MAX:-5}"
    local core_st=""
    [[ "$max" =~ ^[0-9]+$ ]] || max=40
    [[ "$interval" =~ ^[0-9]+$ ]] || interval=3
    (( max < 1 )) && max=1
    (( interval < 1 )) && interval=1
    echo "[$(ts)] ${phase}: health wait tag=${tag} url=${url} retries=${max} interval=${interval}s" >&2
    while (( attempt <= max )); do
      if curl -sf --max-time "$curl_max" "$url" >/dev/null 2>&1; then
        echo "[$(ts)] ${phase}: health OK tag=${tag} attempt=${attempt}/${max}" >&2
        return 0
      fi
      core_st="$(_docker inspect -f '{{.State.Status}}' yedek-core 2>/dev/null || echo missing)"
      if [[ "$core_st" != "running" && "$core_st" != "restarting" ]]; then
        echo "[$(ts)] ${phase}: health aborted tag=${tag} core_status=${core_st} attempt=${attempt}/${max}" >&2
        return 1
      fi
      if (( attempt == 1 || attempt % 5 == 0 || attempt == max )); then
        echo "[$(ts)] ${phase}: health retry tag=${tag} attempt=${attempt}/${max} core=${core_st}" >&2
      fi
      sleep "$interval"
      attempt=$((attempt + 1))
    done
    echo "[$(ts)] ${phase}: health check failed tag=${tag} after ${max} attempts" >&2
    return 1
  }

  if ! wait_for_core_health; then
    return 1
  fi

  # Watcher: once process varligi (systemd stub/RHEL6 yaniltmasin)
  ensure_backup_watcher() {
    if pgrep -f '/yedek/config/backup-watcher\.sh' >/dev/null 2>&1; then
      return 0
    fi
    if command -v systemctl >/dev/null 2>&1; then
      systemctl start yedek-backup-watcher.service 2>/dev/null || true
      systemctl restart yedek-backup-watcher.service 2>/dev/null || true
      sleep 1
      if pgrep -f '/yedek/config/backup-watcher\.sh' >/dev/null 2>&1; then
        return 0
      fi
    fi
    # SysV / fake-systemctl / cron host: dogrudan nohup
    if [[ -x /yedek/config/backup-watcher.sh ]]; then
      nohup /yedek/config/backup-watcher.sh >>/yedek/orayedek/backup-watcher.log 2>&1 9>&- &
      sleep 1
    fi
    pgrep -f '/yedek/config/backup-watcher\.sh' >/dev/null 2>&1
  }

  if ! ensure_backup_watcher; then
    echo "[$(ts)] ${phase}: backup watcher inactive tag=${tag} (uyari — health OK, devam)" >&2
    # Eski davranis deploy'u fail ediyordu; Tavsanli gibi hostlarda false-negative.
    # Health gectiyse release basarili say; watcher'i cron/nohup ile ayaga kaldir.
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

# Latest cozulemeyip eski pin'e dustuyse: calisan daha yeni image varsa downgrade etme
if [[ -z "$FORCE_TAG" && -n "$RUNNING_TAG" ]] && tag_gt "$RUNNING_TAG" "$TARGET_TAG"; then
  echo "[$(ts)] anti-downgrade: target=${TARGET_TAG} < running=${RUNNING_TAG}; running kullaniliyor"
  TARGET_TAG="$RUNNING_TAG"
fi

if [[ "$PREV_TAG" == "$TARGET_TAG" ]]; then
  # Sorun yok gorunse bile fallback pin'i calisan tag ile hizala (oto heal)
  sync_fallback_pin "$TARGET_TAG"
  clear_fail_state
  if needs_compose_recreate; then
    recreate_current_tag "$TARGET_TAG" || exit 1
    exit 0
  fi
  write_state "ok" "Ayni release zaten calisiyor" "$PREV_TAG" "$TARGET_TAG"
  echo "[$(ts)] Ayni release zaten calisiyor (tag=${TARGET_TAG})"
  exit 0
fi

write_state "updating" "Release gecisi basladi" "$PREV_TAG" "$TARGET_TAG"
if deploy_tag "$TARGET_TAG" "deploy"; then
  write_state "ok" "Release guncellendi" "$TARGET_TAG" "$TARGET_TAG"
  clear_fail_state
  # Basarili deploy sonrasi fallback pin her zaman yeni tag (FORCE sart degil)
  sync_fallback_pin "$TARGET_TAG"
  # Hub/manuel --tag sonrasi latest track'e geri ac
  if [[ -n "$FORCE_TAG" && "${RELEASE_UNLOCK_LATEST:-1}" == "1" ]]; then
    unlock_track_latest
    echo "[$(ts)] unlock: RELEASE_TRACK=latest (fallback pin=${TARGET_TAG})"
  fi
  echo "[$(ts)] release ok: ${TARGET_TAG}"
  exit 0
fi

if [[ -n "$PREV_TAG" ]]; then
  write_state "rollback" "Deploy hatasi, rollback deneniyor" "$PREV_TAG" "$TARGET_TAG"
  if deploy_tag "$PREV_TAG" "rollback"; then
    write_state "rolled_back" "Rollback basarili" "$PREV_TAG" "$TARGET_TAG"
    # Rollback sonrasi pin eski calisan tag'de kalsin; yeni broken tag'e kitlenme
    sync_fallback_pin "$PREV_TAG"
    arm_fail_cooldown "$PREV_TAG"
    echo "[$(ts)] rollback ok: ${PREV_TAG}"
    exit 1
  fi
fi

RUN_NOW="$(running_core_tag)"
write_state "failed" "Deploy/rollback basarisiz" "${PREV_TAG:-$RUN_NOW}" "$TARGET_TAG"
arm_fail_cooldown "${RUN_NOW:-$PREV_TAG}"
exit 1
