#!/bin/bash
# Public tek satirlik kurulum giris noktasi.
# Ornek:
#   git clone https://git.trtek.tr/yedek_kontrol_public.git /opt/yedek_kontrol \
#     && ORG_ENROLLMENT_CODE=KOD-ORCL HUB_HTTP_URL=https://hub:8444 \
#        HUB_AGENT_REGISTER_SECRET=... CENTRAL_PROXY_SECRET=... RELEASE_READONLY_TOKEN=... \
#        SETUP_NONINTERACTIVE=1 bash /opt/yedek_kontrol/scripts/bootstrap-public-install.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

[[ "$(id -u)" -eq 0 ]] || { echo "HATA: root olarak calistirin (sudo bash ...)" >&2; exit 1; }

export SETUP_NONINTERACTIVE="${SETUP_NONINTERACTIVE:-1}"
export YEDEK_ROOT="${YEDEK_ROOT:-$ROOT}"

exec bash "$ROOT/setup.sh"
