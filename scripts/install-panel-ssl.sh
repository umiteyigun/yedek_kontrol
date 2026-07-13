#!/bin/bash
# Self-signed SSL + nginx HTTPS proxy for yedek panel (port 8443 -> 127.0.0.1:8090)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSL_DIR="/yedek/ssl"
CERT="${SSL_DIR}/panel.crt"
KEY="${SSL_DIR}/panel.key"
NGINX_CONF="/etc/nginx/conf.d/yedek-panel.conf"
PANEL_HTTPS_PORT="${PANEL_HTTPS_PORT:-8443}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

[[ "$(id -u)" -eq 0 ]] || { echo "Root olarak calistirin" >&2; exit 1; }

if ! command -v nginx >/dev/null 2>&1; then
  log "nginx kuruluyor..."
  if ! rpm -q epel-release >/dev/null 2>&1; then
    yum install -y epel-release || yum install -y --disablerepo=updates epel-release || true
  fi
  yum install -y nginx || yum install -y --disablerepo=updates nginx
fi

mkdir -p "$SSL_DIR"
# nginx kullanicisi traverse edebilmeli (700 = ConnectError / panel down)
chmod 755 "$SSL_DIR"

if [[ ! -f "$CERT" || ! -f "$KEY" ]]; then
  log "Self-signed sertifika uretiliyor: $CERT"
  host_cn="$(hostname -f 2>/dev/null || hostname)"
  host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  san="DNS:${host_cn},DNS:localhost,IP:127.0.0.1"
  if [[ -n "$host_ip" ]]; then
    san="${san},IP:${host_ip}"
  fi
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -keyout "$KEY" \
    -out "$CERT" \
    -subj "/CN=${host_cn}/O=TRTEK Yedek Panel/C=TR" \
    -addext "subjectAltName=${san}" 2>/dev/null \
    || openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
      -keyout "$KEY" \
      -out "$CERT" \
      -subj "/CN=${host_cn}/O=TRTEK Yedek Panel/C=TR"
  chmod 640 "$KEY"
  chmod 644 "$CERT"
  if getent group nginx >/dev/null 2>&1; then
    chown root:nginx "$KEY" "$CERT" || true
  fi
fi

# nginx start oncesi kalici izin/SELinux
if [[ -x "$ROOT/scripts/ensure-panel-ssl-access.sh" ]]; then
  bash "$ROOT/scripts/ensure-panel-ssl-access.sh" || true
elif [[ -x /yedek/config/ensure-panel-ssl-access.sh ]]; then
  bash /yedek/config/ensure-panel-ssl-access.sh || true
fi

install -m 644 "$ROOT/nginx/yedek-panel.conf" "$NGINX_CONF"
sed -i "s/listen 8443 ssl;/listen ${PANEL_HTTPS_PORT} ssl;/" "$NGINX_CONF"

nginx -t
systemctl enable nginx
systemctl restart nginx

ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
ip="${ip:-SUNUCU_IP}"
cat <<EOF

Panel HTTPS hazir:
  https://${ip}:${PANEL_HTTPS_PORT}

Yerel API (yedek.sh) degismedi:
  http://127.0.0.1:8090

Tarayicida self-signed uyarisi normal — devam edin veya sertifikayi guvenilir yapin.
EOF
