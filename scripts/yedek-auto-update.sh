#!/bin/bash
# GitHub'dan yeni commit var mi kontrol eder; varsa yedek-local-deploy.sh calistirir.
# systemd timer ile sessiz calisir — SHA ayniysa log yazmaz.
set -euo pipefail

ROOT="${YEDEK_ROOT:-/opt/yedek_kontrol}"
ENV_FILE="/yedek/config/auto-update.env"
LOG="/var/log/yedek-auto-update.log"
LOCK="/var/run/yedek-auto-update.lock"
DEPLOY_SCRIPT="$ROOT/scripts/yedek-local-deploy.sh"

ts() { date '+%Y-%m-%d %H:%M:%S'; }

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$ENV_FILE"
fi

: "${AUTO_UPDATE_ENABLED:=1}"
: "${AUTO_UPDATE_BRANCH:=main}"

[[ "$AUTO_UPDATE_ENABLED" == "1" ]] || exit 0
command -v git >/dev/null 2>&1 || exit 0
[[ -d "$ROOT/.git" ]] || exit 0
[[ -x "$DEPLOY_SCRIPT" ]] || exit 0

exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

cd "$ROOT"

if [[ -n "${AUTO_UPDATE_GIT_TOKEN:-}" ]]; then
  export GIT_TERMINAL_PROMPT=0
  if [[ "${AUTO_UPDATE_REPO_URL:-}" == https://github.com/* ]]; then
    repo_path="${AUTO_UPDATE_REPO_URL#https://github.com/}"
    repo_path="${repo_path%.git}"
    export GIT_CONFIG_COUNT=1
    export GIT_CONFIG_KEY_0="http.https://github.com/.extraheader"
    export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$AUTO_UPDATE_GIT_TOKEN" | base64 | tr -d '\n')"
  fi
fi

LOCAL_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
[[ -n "$LOCAL_SHA" ]] || exit 0

REMOTE_SHA="$(git ls-remote origin "refs/heads/${AUTO_UPDATE_BRANCH}" 2>/dev/null | awk '{print $1}' | head -1)"
[[ -n "$REMOTE_SHA" ]] || exit 0

if [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
  exit 0
fi

echo "[$(ts)] guncelleme basliyor: ${LOCAL_SHA:0:7} -> ${REMOTE_SHA:0:7}" >>"$LOG"
if YEDEK_ROOT="$ROOT" "$DEPLOY_SCRIPT" >>"$LOG" 2>&1; then
  NEW_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo '?')"
  echo "[$(ts)] tamamlandi: $NEW_SHA" >>"$LOG"
else
  echo "[$(ts)] HATA: deploy basarisiz" >>"$LOG"
  exit 1
fi
