#!/bin/bash
# Private repodan sanitize edilmis public snapshot uretir.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-/tmp/yedek_kontrol_public}"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync gerekli" >&2
  exit 1
fi

rsync -a --delete \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude 'credentials/' \
  --exclude 'config/settings.json' \
  --exclude 'config/sessions.json' \
  --exclude 'config/generated/' \
  --exclude 'config/central-agent.env' \
  --exclude 'config/auto-update.env' \
  --exclude 'config/release-update.env' \
  --exclude '*.log' \
  "$ROOT/" "$OUT_DIR/"

# Hassas sabitleri sanitize et
sanitize_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  sed -i.bak \
    -e 's#https://git\.trtek\.tr#[REDACTED_GIT_HOST]#g' \
    -e 's#git\.trtek\.tr#[REDACTED_GIT_HOST]#g' \
    -e 's#AUTO_UPDATE_GIT_TOKEN=.*#AUTO_UPDATE_GIT_TOKEN=[REDACTED]#g' \
    -e 's#RELEASE_READONLY_TOKEN=.*#RELEASE_READONLY_TOKEN=[REDACTED]#g' \
    -e 's#HUB_AGENT_REGISTER_SECRET=.*#HUB_AGENT_REGISTER_SECRET=[REDACTED]#g' \
    -e 's#CENTRAL_PROXY_SECRET=.*#CENTRAL_PROXY_SECRET=[REDACTED]#g' \
    "$f"
  rm -f "${f}.bak"
}

while IFS= read -r -d '' file; do
  sanitize_file "$file"
done < <(find "$OUT_DIR" -type f \( -name "*.md" -o -name "*.env" -o -name "*.yml" -o -name "*.yaml" -o -name "*.sh" \) -print0)

mkdir -p "$OUT_DIR/config"
cat >"$OUT_DIR/config/settings.public.example.json" <<'EOF'
{
  "hostname": "example-host",
  "yedek_dir": "/yedek/orayedek",
  "instances": []
}
EOF

echo "Public snapshot hazir: $OUT_DIR"
