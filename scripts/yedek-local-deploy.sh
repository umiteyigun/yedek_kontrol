#!/bin/bash
# Git fetch + yerel dosyalari koruyarak merge; gerekirse docker rebuild.
set -euo pipefail

ROOT="${YEDEK_ROOT:-/opt/yedek_kontrol}"
ENV_FILE="/yedek/config/auto-update.env"
LOCAL_FILE="/yedek/config/auto-update.local.sh"
STAGING=""

cleanup() {
  [[ -n "$STAGING" && -d "$STAGING" ]] && rm -rf "$STAGING"
}
trap cleanup EXIT

ts() { date '+%Y-%m-%d %H:%M:%S'; }

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi

: "${AUTO_UPDATE_BRANCH:=main}"

PRESERVE=(
  config/settings.json
  config/sessions.json
  config/generated
  credentials
  .env
)

if [[ -f "$LOCAL_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$LOCAL_FILE"
  if [[ -n "${AUTO_UPDATE_PRESERVE:-}" ]]; then
    PRESERVE=("${AUTO_UPDATE_PRESERVE[@]}")
  fi
fi

[[ -d "$ROOT/.git" ]] || { echo "[$(ts)] HATA: git repo yok: $ROOT"; exit 1; }
cd "$ROOT"

OLD_SHA="$(git rev-parse HEAD)"
STAGING="$(mktemp -d /tmp/yedek-auto-update-staging.XXXXXX)"
mkdir -p "$STAGING/preserve"

for rel in "${PRESERVE[@]}"; do
  [[ -z "$rel" ]] && continue
  [[ -e "$ROOT/$rel" ]] || continue
  mkdir -p "$STAGING/preserve/$(dirname "$rel")"
  cp -a "$ROOT/$rel" "$STAGING/preserve/$rel"
done

if [[ -n "${AUTO_UPDATE_GIT_TOKEN:-}" ]]; then
  export GIT_TERMINAL_PROMPT=0
  if [[ "${AUTO_UPDATE_REPO_URL:-}" == https://github.com/* ]]; then
    export GIT_CONFIG_COUNT=1
    export GIT_CONFIG_KEY_0="http.https://github.com/.extraheader"
    export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$AUTO_UPDATE_GIT_TOKEN" | base64 | tr -d '\n')"
  fi
fi

git fetch origin "$AUTO_UPDATE_BRANCH"
if ! git merge --ff-only "origin/$AUTO_UPDATE_BRANCH"; then
  echo "[$(ts)] ff-only basarisiz, hard reset: origin/$AUTO_UPDATE_BRANCH"
  git reset --hard "origin/$AUTO_UPDATE_BRANCH"
fi

for rel in "${PRESERVE[@]}"; do
  [[ -z "$rel" ]] && continue
  [[ -e "$STAGING/preserve/$rel" ]] || continue
  mkdir -p "$ROOT/$(dirname "$rel")"
  cp -a "$STAGING/preserve/$rel" "$ROOT/$rel"
done

install -m 755 "$ROOT/scripts/yedek.sh" /usr/bin/yedek.sh
ln -sfn /usr/bin/yedek.sh /usr/bin/yedek2.sh

CHANGED="$(git diff --name-only "$OLD_SHA" HEAD 2>/dev/null || true)"
needs_core=0
needs_agent=0

while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  case "$f" in
    core/*|docker-compose.yml) needs_core=1 ;;
    agent/*) needs_agent=1 ;;
  esac
done <<<"$CHANGED"

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

if [[ "$needs_core" -eq 1 ]]; then
  echo "[$(ts)] core rebuild"
  compose up -d --build core
fi

if [[ "$needs_agent" -eq 1 && -f /yedek/config/central-agent.env ]]; then
  # shellcheck source=/dev/null
  source /yedek/config/central-agent.env
  if [[ -n "${ORG_ENROLLMENT_CODE:-}" ]]; then
    echo "[$(ts)] central-agent rebuild"
    compose --profile central up -d --build central-agent
  fi
fi

if [[ -f "$LOCAL_FILE" && -n "${AUTO_UPDATE_POST_DEPLOY:-}" ]]; then
  for cmd in "${AUTO_UPDATE_POST_DEPLOY[@]}"; do
    (cd "$ROOT" && eval "$cmd")
  done
fi

echo "[$(ts)] deploy bitti: $(git rev-parse --short HEAD)"
