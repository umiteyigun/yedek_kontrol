#!/bin/bash
# =============================================================================
# yedek-docker kurulum scripti
# Kullanim: bash setup.sh
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
fail() { echo "HATA: $*" >&2; exit 1; }

[[ "$(id -u)" -eq 0 ]] || fail "Bu script root olarak calistirilmalidir."

# --- 1. Docker kurulumu ---
install_docker() {
  if command -v docker >/dev/null 2>&1; then
    log "Docker mevcut: $(docker --version)"
    return 0
  fi

  log "Docker bulunamadi, kurulum basliyor (CentOS/RHEL 7)..."
  if ! command -v yum >/dev/null 2>&1; then
    fail "yum bulunamadi. Desteklenen: CentOS/RHEL 7"
  fi

  if ! rpm -q device-mapper-persistent-data >/dev/null 2>&1 || ! rpm -q lvm2 >/dev/null 2>&1; then
    yum install -y yum-utils device-mapper-persistent-data lvm2 || \
      yum install -y --disablerepo=updates yum-utils device-mapper-persistent-data lvm2 || \
      log "UYARI: bazi bagimliliklar kurulamadi, devam ediliyor..."
  else
    log "device-mapper-persistent-data ve lvm2 mevcut, guncelleme atlaniyor"
    command -v yum-config-manager >/dev/null 2>&1 || yum install -y yum-utils
  fi

  if [ ! -f /etc/yum.repos.d/docker-ce.repo ]; then
    log "Docker CE reposu ekleniyor..."
    yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
  fi

  yum install -y --disablerepo=updates docker-ce docker-ce-cli containerd.io docker-compose-plugin || \
    yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

  systemctl enable docker
  systemctl start docker
  log "Docker kuruldu: $(docker --version)"
}

ensure_docker_running() {
  systemctl enable docker 2>/dev/null || true
  if ! systemctl is-active --quiet docker; then
    log "Docker servisi baslatiliyor..."
    systemctl start docker
  fi
}

check_compose() {
  if docker compose version >/dev/null 2>&1; then
    log "Docker Compose: $(docker compose version)"
    return 0
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    log "docker-compose (legacy): $(docker-compose --version)"
    COMPOSE_CMD="docker-compose"
    return 0
  fi
  fail "Docker Compose bulunamadi"
}

COMPOSE_CMD="${COMPOSE_CMD:-docker compose}"

compose() {
  if [ "$COMPOSE_CMD" = "docker compose" ]; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

# --- 2. Dizinler ve bos config ---
prepare_config() {
  log "Dizinler hazirlaniyor..."
  mkdir -p /yedek/config /yedek/orayedek
  chown oracle:oinstall /yedek/config /yedek/orayedek 2>/dev/null || true

  if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    log ".env olusturuldu (panel giris bilgileri)"
  fi

  if [ ! -f "$ROOT/config/settings.json" ]; then
    cp "$ROOT/config/settings.empty.json" "$ROOT/config/settings.json"
    log "settings.json bos degerlerle olusturuldu"
  fi

  bash "$ROOT/scripts/generate-master-credentials.sh"
}

# --- 2b. Host paketleri (zip vb.) ---
install_host_packages() {
  log "Host paketleri kontrol ediliyor (zip, unzip, ftp)..."
  if command -v yum >/dev/null 2>&1; then
    yum install -y zip unzip ftp || log "UYARI: zip/unzip/ftp kurulamadi"
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y zip unzip || log "UYARI: zip/unzip kurulamadi"
  else
    log "UYARI: zip kurulumu atlandi (yum/apt yok)"
  fi
  if command -v zip >/dev/null 2>&1; then
    log "zip hazir: $(zip -v 2>/dev/null | head -1 || echo ok)"
  else
    log "UYARI: zip komutu bulunamadi — Zip sifreli yedek modu calismaz"
  fi
}

# --- 3. Host scriptleri ve watcher ---
install_host_scripts() {
  log "Host yedek scriptleri kuruluyor..."

  # Eski scriptleri yedekle
  for old in /usr/bin/yedek.sh /usr/bin/yedek2.sh /usr/bin/yedekconfig.sh /usr/bin/yedekconfig2.sh; do
    if [[ -f "$old" && ! -L "$old" ]]; then
      cp -a "$old" "/yedek/config/$(basename "$old").bak.$(date +%s)"
      log "Yedeklendi: $old"
    fi
  done

  # Ana yedek scripti - bizim surum
  install -m 755 "$ROOT/scripts/yedek.sh" /usr/bin/yedek.sh
  ln -sfn /usr/bin/yedek.sh /usr/bin/yedek2.sh
  log "Kuruldu: /usr/bin/yedek.sh (yedek2.sh -> symlink)"

  install -m 755 "$ROOT/scripts/run-backup.sh" /yedek/config/run-backup.sh
  install -m 755 "$ROOT/scripts/backup-watcher.sh" /yedek/config/backup-watcher.sh
  install -m 755 "$ROOT/scripts/oracle-probe.sh" /yedek/config/oracle-probe.sh
  install -m 755 "$ROOT/scripts/oracle-schemas.sh" /yedek/config/oracle-schemas.sh
  install -m 755 "$ROOT/scripts/oracle-rman-probe.sh" /yedek/config/oracle-rman-probe.sh
  install -m 755 "$ROOT/scripts/rman.sh" /usr/bin/rman.sh
  install -m 755 "$ROOT/scripts/run-rman.sh" /yedek/config/run-rman.sh
  install -m 755 "$ROOT/scripts/host-info.sh" /yedek/config/host-info.sh
  install -m 755 "$ROOT/scripts/host-timezone.sh" /yedek/config/host-timezone.sh
  install -m 755 "$ROOT/scripts/oracle-stats.sh" /yedek/config/oracle-stats.sh
  install -m 755 "$ROOT/scripts/disk-check-backup.sh" /yedek/config/disk-check-backup.sh
  install -m 755 "$ROOT/scripts/disk-report.sh" /yedek/config/disk-report.sh
  install -m 755 "$ROOT/scripts/terminal-shell.sh" /yedek/config/terminal-shell.sh
  install -m 644 "$ROOT/scripts/yedek-web-terminal-profile.sh" /yedek/config/yedek-web-terminal-profile.sh
  install -m 644 "$ROOT/scripts/yedek-web-terminal-guard.sh" /yedek/config/yedek-web-terminal-guard.sh
  install -m 644 "$ROOT/scripts/99-yedek-web-terminal.sh" /etc/profile.d/99-yedek-web-terminal.sh
  install -d -m 755 /yedek/config/terminal-bin
  install -m 755 "$ROOT/scripts/terminal-bin/yedek-terminal-blocked" /yedek/config/terminal-bin/yedek-terminal-blocked
  for _cmd in passwd chpasswd chage vipw vigr htpasswd; do
    ln -sfn /yedek/config/terminal-bin/yedek-terminal-blocked "/yedek/config/terminal-bin/${_cmd}"
  done

  install -m 755 "$ROOT/scripts/install-panel-ssl.sh" /yedek/config/install-panel-ssl.sh

  # su - oracle icin Last login satirini gizle (SSH etkilenmez, dosya bos)
  touch /root/.hushlogin 2>/dev/null || true
  if id oracle &>/dev/null; then
    touch /home/oracle/.hushlogin 2>/dev/null || true
    chown oracle:oinstall /home/oracle/.hushlogin 2>/dev/null \
      || chown oracle:dba /home/oracle/.hushlogin 2>/dev/null || true
  fi

  ln -sfn /yedek/config/yedekconfig.sh /usr/bin/yedekconfig.sh
  ln -sfn /yedek/config/yedekconfig.sh /usr/bin/yedekconfig2.sh

  bash "$ROOT/scripts/bootstrap-config.sh"

  cat >/etc/systemd/system/yedek-backup-watcher.service <<'UNIT'
[Unit]
Description=Yedek backup trigger watcher
After=network.target yedek-docker.service docker.service
Wants=yedek-docker.service

[Service]
Type=simple
ExecStart=/yedek/config/backup-watcher.sh
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
}

install_systemd_services() {
  log "systemd servisleri kuruluyor (reboot sonrasi otomatik baslatma)..."
  chmod +x "$ROOT/scripts/yedek-docker-ctl.sh"

  sed "s|__YEDEK_ROOT__|${ROOT}|g" "$ROOT/scripts/yedek-docker.service.tpl" \
    >/etc/systemd/system/yedek-docker.service

  systemctl daemon-reload
  systemctl enable docker.service
  systemctl enable yedek-docker.service
  systemctl enable yedek-backup-watcher.service
  systemctl restart yedek-backup-watcher.service
  log "enable: docker, yedek-docker, yedek-backup-watcher"
}

# --- Merkez agent (yedek-docker ile kurulur) ---
prepare_central_agent_config() {
  local dst="/yedek/config/central-agent.env"
  local example="$ROOT/config/central-agent.example.env"
  mkdir -p /yedek/config/agent-state

  if [[ -f "$dst" ]]; then
    log "Merkez agent config mevcut: $dst"
    return 0
  fi

  if [[ -n "${ORG_ENROLLMENT_CODE:-}" ]]; then
    local hub_http="${HUB_HTTP_URL:-http://127.0.0.1:8444}"
    local hub_ws="${HUB_WS_URL:-${hub_http/http/ws}/agent/v1}"
    hub_ws="${hub_ws/https/wss}"
    cat >"$dst" <<EOF
ORG_ENROLLMENT_CODE=${ORG_ENROLLMENT_CODE}
HUB_HTTP_URL=${hub_http}
HUB_WS_URL=${hub_ws}
PANEL_LOCAL_URL=https://127.0.0.1:8443
NODE_LABEL=${NODE_LABEL:-primary}
NODE_ROLE=${NODE_ROLE:-PRIMARY}
AGENT_VERIFY_TLS=0
AGENT_STATE_DIR=/var/lib/yedek-agent
EOF
    chmod 600 "$dst"
    log "Merkez agent config olusturuldu: $dst"
    return 0
  fi

  if [[ -f "$example" ]]; then
    install -m 600 "$example" "$dst"
    log "Merkez agent sablonu: $dst (ORG_ENROLLMENT_CODE doldurun, sonra: compose --profile central up -d)"
  fi
}

start_central_agent() {
  local cfg="/yedek/config/central-agent.env"
  [[ -f "$cfg" ]] || return 0
  # shellcheck source=/dev/null
  source "$cfg"
  [[ -n "${ORG_ENROLLMENT_CODE:-}" ]] || {
    log "Merkez agent atlandi: $cfg icinde ORG_ENROLLMENT_CODE bos"
    return 0
  }
  log "yedek-central-agent build ve baslatiliyor..."
  compose --profile central up -d --build central-agent
  sleep 2
  if docker ps --format '{{.Names}}' | grep -q '^yedek-central-agent$'; then
    log "yedek-central-agent calisiyor (hub onayi bekleniyor olabilir)"
  else
    log "UYARI: yedek-central-agent baslatilamadi — logs: docker compose logs central-agent"
  fi
}

# --- 4. Docker compose ---
start_stack() {
  log "Compose dosyasi kontrol ediliyor..."
  compose config >/dev/null

  log "yedek-core build ve baslatiliyor..."
  compose up -d --build core

  sleep 3
  if docker ps --format '{{.Names}}' | grep -q '^yedek-core$'; then
    log "yedek-core calisiyor"
  else
    fail "yedek-core baslatilamadi. Log: docker compose -f $ROOT/docker-compose.yml logs"
  fi

  start_central_agent
}

print_summary() {
  local ip
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  ip="${ip:-SUNUCU_IP}"

  cat <<EOF

================================================================================
  KURULUM TAMAMLANDI
================================================================================
  Docker    : $(docker --version 2>/dev/null || echo '-')
  Compose   : $($COMPOSE_CMD version 2>/dev/null | head -1 || echo '-')

  Web Panel : https://${ip}:${PANEL_HTTPS_PORT:-8443}  (self-signed SSL)
  Yerel API : http://127.0.0.1:8090  (yedek.sh — degismedi)
  Master    : credentials/master.txt dosyasina bakin

  Giris:
    - Master kullanici (LDAP kapaliyken veya acil erisim)
    - Kurumsal LDAP (panelden LDAP ayarlari acilinca)

  Sonraki adimlar:
    1. Panele girin -> /etc/oratab'taki Oracle instance'lar otomatik eklenir
    2. Ayarlar -> kurum adlari ve Oracle sifrelerini duzenleyin, kaydedin
    3. Yedekler -> Gunluk Yedek Baslat (ilk yedek icin)

  Kurulan scriptler:
    /usr/bin/yedek.sh      (TRTEK v2 - panel config kullanir)
    /usr/bin/yedek2.sh     -> yedek.sh
    /usr/bin/yedekconfig.sh -> /yedek/config/yedekconfig.sh

  Systemd (reboot sonrasi otomatik):
    yedek-docker.service         -> docker stack (panel, API, FTP)
    yedek-backup-watcher.service -> panelden yedek tetikleme
    docker.service               -> container motoru

  Komutlar:
    systemctl status yedek-docker yedek-backup-watcher
    systemctl restart yedek-docker
    docker compose -f $ROOT/docker-compose.yml logs -f core
    docker compose -f $ROOT/docker-compose.yml logs -f central-agent

  Merkez agent (kurum kodu /yedek/config/central-agent.env):
    ORG_ENROLLMENT_CODE=... HUB_HTTP_URL=... bash setup.sh
    docker compose --profile central up -d central-agent
    journalctl -u yedek-backup-watcher -f
================================================================================
EOF
}

main() {
  log "=== yedek-docker setup basladi ==="
  install_docker
  ensure_docker_running
  check_compose
  prepare_config
  install_host_packages
  install_host_scripts
  prepare_central_agent_config
  start_stack
  bash "$ROOT/scripts/install-panel-ssl.sh" || log "UYARI: HTTPS nginx kurulumu atlandi veya basarisiz"
  install_systemd_services
  print_summary
  log "=== setup bitti ==="
}

main "$@"
