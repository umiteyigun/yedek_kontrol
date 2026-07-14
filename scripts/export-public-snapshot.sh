#!/bin/bash
# Private repodan public runtime snapshot uretir.
# Public repo: docker image cekip kurulum icin gerekli dosyalar (kaynak kod yok).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-/tmp/yedek_kontrol_public}"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

if ! command -v rsync >/dev/null 2>&1; then
  echo "rsync gerekli" >&2
  exit 1
fi

# Runtime / deploy dosyalari (uygulama kaynagi haric)
rsync -a \
  "$ROOT/setup.sh" \
  "$ROOT/.env.example" \
  "$ROOT/.gitignore" \
  "$ROOT/docker-compose.yml" \
  "$ROOT/nginx/" \
  "$ROOT/vsftpd/" \
  "$OUT_DIR/"

mkdir -p "$OUT_DIR/config" "$OUT_DIR/scripts" "$OUT_DIR/docs"
rsync -a \
  --exclude 'settings.json' \
  --exclude 'sessions.json' \
  --exclude 'generated/' \
  --exclude 'central-agent.env' \
  --exclude 'auto-update.env' \
  --exclude 'release-update.env' \
  --exclude 'release-state.json' \
  "$ROOT/config/" "$OUT_DIR/config/"

rsync -a \
  --exclude 'export-public-snapshot.sh' \
  "$ROOT/scripts/" "$OUT_DIR/scripts/"

rsync -a "$ROOT/docs/repo-split-and-public-plan.md" "$OUT_DIR/docs/"

mkdir -p "$OUT_DIR/config/templates"
rsync -a "$ROOT/core/app/config/templates/" "$OUT_DIR/config/templates/"

# Public docker-compose: build yok, image env ile
cat >"$OUT_DIR/docker-compose.yml" <<'EOF'
# Public runtime: image registry'den cekilir (setup.sh + release-updater).
# RELEASE_TARGET_TAG ve image adlari: /yedek/config/release-update.env

services:
  core:
    image: ${RELEASE_CORE_IMAGE}:${RELEASE_IMAGE_TAG}
    container_name: yedek-core
    restart: unless-stopped
    network_mode: host
    privileged: true
    pid: host
    volumes:
      - ./config:/app/config
      - /yedek/config:/host-output
      - /yedek:/yedek
      - /yedek/config/yedekconfig.sh:/host-config/yedekconfig.sh:ro
    environment:
      CONFIG_DIR: /app/config
      HOST_OUTPUT: /host-output
      YEDEK_DIR: /yedek/orayedek
      HOST_YEDEKCONFIG: /host-config/yedekconfig.sh
      PANEL_USER: ${MASTER_USER:-trtek-master}
      PANEL_PASS: ${MASTER_PASS:-}
      MASTER_USER: ${MASTER_USER:-trtek-master}
      MASTER_PASS: ${MASTER_PASS:-}
      PANEL_SECRET: ${PANEL_SECRET:-degistirin}
      SESSION_MAX_AGE: ${SESSION_MAX_AGE:-7200}
      TERMINAL_SESSION_MAX_AGE: ${TERMINAL_SESSION_MAX_AGE:-900}
      TERMINAL_MAX_SEC: ${TERMINAL_MAX_SEC:-1800}
      TERMINAL_MAX_SESSIONS: ${TERMINAL_MAX_SESSIONS:-10}
      TERMINAL_MAX_PER_USER: ${TERMINAL_MAX_PER_USER:-0}
      COOKIE_SECURE: ${COOKIE_SECURE:-0}
      SESSION_BIND_IP: ${SESSION_BIND_IP:-1}
      SESSION_BIND_UA: ${SESSION_BIND_UA:-1}
      LOGIN_MAX_FAILURES: ${LOGIN_MAX_FAILURES:-10}
      LOGIN_LOCKOUT_SEC: ${LOGIN_LOCKOUT_SEC:-900}
      PANEL_SERVER_HEADER: ${PANEL_SERVER_HEADER:-YedekPanel}
      TZ: ${TZ:-Europe/Istanbul}
    env_file:
      - /yedek/config/central-agent.env

  central-agent:
    image: ${RELEASE_AGENT_IMAGE}:${RELEASE_IMAGE_TAG}
    container_name: yedek-central-agent
    restart: unless-stopped
    network_mode: host
    env_file:
      - /yedek/config/central-agent.env
    volumes:
      - /yedek/config/agent-state:/var/lib/yedek-agent
    profiles:
      - central
EOF

cat >"$OUT_DIR/README.md" <<'EOF'
# yedek_kontrol_public

Oracle yedek istemcisi **runtime** deposu. Uygulama kaynak kodu burada yok;
`yedek-core` ve `yedek-central-agent` image'lari registry'den cekilir.

## Icindekiler

- `setup.sh` — Docker, host scriptleri, systemd, panel kurulumu
- `scripts/` — `yedek.sh`, backup watcher, release-updater, Oracle probe vb.
- `config/*.example` — ornek env ve ayar sablonlari
- `docker-compose.yml` — image tabanli stack tanimi
- `nginx/`, `vsftpd/` — panel/FTP yardimci config

## Kurulum

```bash
git clone https://git.trtek.tr/yedek_kontrol_public.git /opt/yedek_kontrol
cd /opt/yedek_kontrol

# Registry ve hedef tag (ornek)
mkdir -p /yedek/config
cp config/release-update.example.env /yedek/config/release-update.env
# RELEASE_TARGET_TAG, RELEASE_READONLY_TOKEN, RELEASE_CORE_IMAGE doldur

sudo bash setup.sh
```

Kurulum sonrasi master sifre **repo icinde degil**:
`/yedek/credentials/master.txt` (chmod 600).

## Guncelleme

Image release akisi `scripts/release-updater.sh` + `yedek-release-update.timer` ile calisir.
Hedef tag `/yedek/config/release-update.env` icindeki `RELEASE_TARGET_TAG` alanindan okunur.

## Private gelistirme

Uygulama kaynagi ve CI build: private `yedek_kontrol` reposu.
EOF

cat >"$OUT_DIR/config/settings.public.example.json" <<'EOF'
{
  "hostname": "example-host",
  "yedek_dir": "/yedek/orayedek",
  "instances": []
}
EOF

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

echo "Public runtime snapshot hazir: $OUT_DIR"
echo "  - kaynak kod (core/agent) dahil degil"
echo "  - setup + host scriptleri + image compose dahil"
