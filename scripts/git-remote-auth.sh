#!/bin/bash
# Git HTTPS kimlik dogrulama — GitHub ve git.trtek.tr (Gitea) destekler.
# Kaynak: /yedek/config/auto-update.env (AUTO_UPDATE_REPO_URL, AUTO_UPDATE_GIT_TOKEN)
set -euo pipefail

setup_git_remote_auth() {
  local repo_url="${AUTO_UPDATE_REPO_URL:-}"
  local token="${AUTO_UPDATE_GIT_TOKEN:-}"
  local cred_file="${GIT_CREDENTIAL_FILE:-/yedek/config/.git-credentials}"

  [[ -n "$token" ]] || return 0
  export GIT_TERMINAL_PROMPT=0

  if [[ "$repo_url" == https://github.com/* ]]; then
    export GIT_CONFIG_COUNT=1
    export GIT_CONFIG_KEY_0="http.https://github.com/.extraheader"
    export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic $(printf 'x-access-token:%s' "$token" | base64 | tr -d '\n')"
    return 0
  fi

  if [[ "$repo_url" =~ ^https://([^/]+)/ ]]; then
    local host="${BASH_REMATCH[1]}"
    mkdir -p "$(dirname "$cred_file")"
    printf 'https://oauth2:%s@%s\n' "$token" "$host" >"$cred_file"
    chmod 600 "$cred_file"
    export GIT_CONFIG_COUNT=2
    export GIT_CONFIG_KEY_0="credential.helper"
    export GIT_CONFIG_VALUE_0="store --file=${cred_file}"
    # centos proxy uzerinden fake cert ile erisim (FortiGate .166 kapali)
    if [[ "$host" == "git.trtek.tr" ]]; then
      export GIT_CONFIG_KEY_1="http.https://git.trtek.tr/.sslVerify"
      export GIT_CONFIG_VALUE_1="false"
    fi
  fi
}
