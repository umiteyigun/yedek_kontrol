#!/bin/bash
# settings.json -> /yedek/config/yedekconfig.sh + yedek-params.sh (host bootstrap)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETTINGS="${ROOT}/config/settings.json"
if [[ -d "${ROOT}/config/templates" ]]; then
  TPL="${ROOT}/config/templates"
else
  TPL="${ROOT}/core/app/config/templates"
fi
OUT="/yedek/config"

[[ -f "$SETTINGS" ]] || { echo "settings.json yok: $SETTINGS" >&2; exit 1; }

get_str() {
  python -c "import json; print(json.load(open('$SETTINGS')).get('$1',''))" 2>/dev/null || \
  grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" "$SETTINGS" | head -1 | sed 's/.*: *"\([^"]*\)".*/\1/'
}

get_int() {
  python -c "import json; print(json.load(open('$SETTINGS')).get('$1',0))" 2>/dev/null || \
  grep -o "\"$1\"[[:space:]]*:[[:space:]]*[0-9]*" "$SETTINGS" | head -1 | sed 's/.*: *//'
}

render() {
  local tpl="$1" out="$2"
  local content
  content="$(cat "$tpl")"
  local keys=(
    hastane il password schemas hostname kurumkodu directory directorydizini
    oracle_ver oracle_sid localftpip localftpuser localftppass
    yedek_kodu guid_key yedek_dir pasv_address core_port
  )
  for key in "${keys[@]}"; do
    local val
    val="$(get_str "$key")"
    content="${content//\{\{ ${key} \}\}/$val}"
  done
  printf '%s\n' "$content" >"$out"
  chmod 755 "$out"
}

mkdir -p "$OUT"

if command -v python >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then
  :
else
  echo "UYARI: python yok, sed ile sinirli render" >&2
fi

render "${TPL}/yedekconfig.sh.tpl" "${OUT}/yedekconfig.sh"
render "${TPL}/yedek-params.sh.tpl" "${OUT}/yedek-params.sh"
echo "Bootstrap config: ${OUT}/yedekconfig.sh ${OUT}/yedek-params.sh"
