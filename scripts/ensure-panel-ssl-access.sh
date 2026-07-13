#!/bin/bash
# nginx baslamadan once /yedek/ssl izin + SELinux duzeltmesi.
# Uskudar vakasi: ssl dir 700 olunca nginx cert okuyamaz -> 8443 down -> agent ConnectError.
set -euo pipefail

SSL_DIR="${YEDEK_SSL_DIR:-/yedek/ssl}"
CERT="${SSL_DIR}/panel.crt"
KEY="${SSL_DIR}/panel.key"

[[ "$(id -u)" -eq 0 ]] || exit 0

mkdir -p "$SSL_DIR"

# Kritik: nginx (unprivileged) traverse edebilmeli — ASLA 700 degil
chmod 755 "$SSL_DIR" 2>/dev/null || true

if [[ -f "$CERT" ]]; then
  chmod 644 "$CERT" 2>/dev/null || true
fi
if [[ -f "$KEY" ]]; then
  chmod 640 "$KEY" 2>/dev/null || true
fi

# nginx group varsa sahip ayarla
if getent group nginx >/dev/null 2>&1; then
  chown root:nginx "$CERT" "$KEY" 2>/dev/null || true
elif getent group www-data >/dev/null 2>&1; then
  chown root:www-data "$CERT" "$KEY" 2>/dev/null || true
else
  chown root:root "$CERT" "$KEY" 2>/dev/null || true
fi

# SELinux (varsa)
if command -v getenforce >/dev/null 2>&1; then
  enf="$(getenforce 2>/dev/null || true)"
  if [[ "$enf" == "Enforcing" || "$enf" == "Permissive" ]]; then
    if command -v semanage >/dev/null 2>&1; then
      semanage fcontext -a -t httpd_sys_content_t "${SSL_DIR}(/.*)?" 2>/dev/null \
        || semanage fcontext -m -t httpd_sys_content_t "${SSL_DIR}(/.*)?" 2>/dev/null \
        || true
      # 8443 zaten http_port_t listesinde olabilir
      semanage port -a -t http_port_t -p tcp 8443 2>/dev/null \
        || semanage port -m -t http_port_t -p tcp 8443 2>/dev/null \
        || true
    fi
    if command -v restorecon >/dev/null 2>&1; then
      restorecon -Rv "$SSL_DIR" >/dev/null 2>&1 || true
    elif command -v chcon >/dev/null 2>&1; then
      chcon -R -t httpd_sys_content_t "$SSL_DIR" 2>/dev/null || true
    fi
  fi
fi

# pid dosyasi icin (bazi hostlarda SELinux engeli)
if [[ -d /var/run ]]; then
  mkdir -p /var/run/nginx 2>/dev/null || true
  if command -v chcon >/dev/null 2>&1; then
    chcon -t httpd_var_run_t /var/run/nginx 2>/dev/null || true
    [[ -f /var/run/nginx.pid ]] && chcon -t httpd_var_run_t /var/run/nginx.pid 2>/dev/null || true
  fi
fi

exit 0
