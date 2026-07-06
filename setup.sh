#!/bin/bash
# =============================================================================
# yedek-docker kurulum scripti
# Kullanim: bash setup.sh
# Non-interactive: SETUP_NONINTERACTIVE=1 bash setup.sh
# Agent env ile: ORG_ENROLLMENT_CODE=... HUB_HTTP_URL=... bash setup.sh
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# shellcheck source=scripts/setup-prompts.sh
source "$ROOT/scripts/setup-prompts.sh"
PROMPT_ROOT="$ROOT"

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
}

# --- 2b. Host paketleri ---
install_host_packages() {
  log "Host paketleri kontrol ediliyor (zip, git, python, openssl, ftp)..."
  if command -v yum >/dev/null 2>&1; then
    yum install -y zip unzip ftp git openssl util-linux python || \
      yum install -y --disablerepo=updates zip unzip ftp git openssl util-linux python || \
      log "UYARI: bazi host paketleri kurulamadi"
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y zip unzip git openssl util-linux python3 || \
      log "UYARI: bazi host paketleri kurulamadi"
  else
    log "UYARI: paket kurulumu atlandi (yum/apt yok)"
  fi
  for need in zip git python flock openssl; do
    if command -v "$need" >/dev/null 2>&1; then
      log "$need hazir"
    else
      log "UYARI: $need bulunamadi — panel/yedek veya oto-guncelleme etkilenebilir"
    fi
  done
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

  bash "$ROOT/scripts/install-host-scripts.sh"
  log "Host scriptleri /yedek/config altina kuruldu"

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
  chmod +x "$ROOT/scripts/yedek-auto-update.sh" "$ROOT/scripts/yedek-local-deploy.sh"

  sed "s|__YEDEK_ROOT__|${ROOT}|g" "$ROOT/scripts/yedek-docker.service.tpl" \
    >/etc/systemd/system/yedek-docker.service

  systemctl daemon-reload
  systemctl enable docker.service
  systemctl enable yedek-docker.service
  systemctl enable yedek-backup-watcher.service
  systemctl restart yedek-backup-watcher.service
  log "enable: docker, yedek-docker, yedek-backup-watcher"
}

prepare_auto_update_config() {
  local dst="/yedek/config/auto-update.env"
  local local_dst="/yedek/config/auto-update.local.sh"
  local example="$ROOT/config/auto-update.example.env"
  local local_example="$ROOT/config/auto-update.local.example.sh"

  if [[ ! -f "$dst" && -f "$example" ]]; then
    install -m 600 "$example" "$dst"
    sed -i "s|^# YEDEK_ROOT=.*|YEDEK_ROOT=${ROOT}|" "$dst" 2>/dev/null || true
    if ! grep -q "^YEDEK_ROOT=" "$dst" 2>/dev/null; then
      echo "YEDEK_ROOT=${ROOT}" >>"$dst"
    fi
    log "Auto-update config olusturuldu: $dst"
  elif [[ -f "$dst" ]] && ! grep -q "^YEDEK_ROOT=" "$dst" 2>/dev/null; then
    echo "YEDEK_ROOT=${ROOT}" >>"$dst"
  fi

  if [[ ! -f "$local_dst" && -f "$local_example" ]]; then
    install -m 600 "$local_example" "$local_dst"
    log "Auto-update yerel koruma listesi: $local_dst"
  fi
}

install_auto_update_timer() {
  if [[ ! -d "$ROOT/.git" ]]; then
    log "Auto-update atlandi: $ROOT git repo degil (tarball kurulum?)"
    return 0
  fi

  prepare_auto_update_config

  sed "s|__YEDEK_ROOT__|${ROOT}|g" "$ROOT/scripts/yedek-auto-update.service.tpl" \
    >/etc/systemd/system/yedek-auto-update.service
  cp "$ROOT/scripts/yedek-auto-update.timer.tpl" /etc/systemd/system/yedek-auto-update.timer

  touch /var/log/yedek-auto-update.log
  chmod 640 /var/log/yedek-auto-update.log

  systemctl daemon-reload
  systemctl enable yedek-auto-update.timer
  systemctl start yedek-auto-update.timer
  log "enable: yedek-auto-update.timer (her ~2dk git.trtek.tr kontrol)"
}

# --- Merkez agent (yedek-docker ile kurulur) ---
prepare_central_agent_config() {
  local dst="/yedek/config/central-agent.env"
  local example="$ROOT/config/central-agent.example.env"
  mkdir -p /yedek/config/agent-state

  if [[ -f "$dst" ]]; then
    # shellcheck source=/dev/null
    source "$dst"
    if [[ -n "${ORG_ENROLLMENT_CODE:-}" ]]; then
      log "Merkez agent config mevcut: $dst"
      return 0
    fi
    log "Merkez agent sablonu mevcut (ORG_ENROLLMENT_CODE bos): $dst"
    return 0
  fi

  if [[ -n "${ORG_ENROLLMENT_CODE:-}" ]]; then
    local hub_http="${HUB_HTTP_URL:-http://127.0.0.1:8444}"
    local hub_ws="${HUB_WS_URL:-${hub_http/http/ws}/agent/v1}"
    hub_ws="${hub_ws/https/wss}"
    write_central_agent_env \
      "$ORG_ENROLLMENT_CODE" "$hub_http" "$hub_ws" \
      "${HUB_AGENT_REGISTER_SECRET:-}" "${CENTRAL_PROXY_SECRET:-}" \
      "${NODE_LABEL:-primary}" "${NODE_ROLE:-PRIMARY}"
    log "Merkez agent config olusturuldu: $dst"
    return 0
  fi

  if [[ -f "$example" ]]; then
    install -m 600 "$example" "$dst"
    log "Merkez agent sablonu: $dst"
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
    yedek-auto-update.timer      -> git.trtek.tr commit kontrolu (~2dk)
    docker.service               -> container motoru

  Dogrulama:
    bash $ROOT/scripts/verify-client-setup.sh

  Komutlar:
    systemctl status yedek-docker yedek-backup-watcher
    systemctl status yedek-auto-update.timer
    tail -f /var/log/yedek-auto-update.log
    systemctl restart yedek-docker
    docker compose -f $ROOT/docker-compose.yml logs -f core
    docker compose -f $ROOT/docker-compose.yml logs -f central-agent

  Merkez agent (kurum kodu /yedek/config/central-agent.env):
    ORG_ENROLLMENT_CODE=... HUB_HTTP_URL=... bash setup.sh
    docker compose --profile central up -d central-agent
    journalctl -u yedek-backup-watcher -f
================================================================================
EOF
  print_config_hints
}

main() {
  log "=== yedek-docker setup basladi ==="
  install_docker
  ensure_docker_running
  check_compose
  prepare_config
  interactive_configure
  bash "$ROOT/scripts/generate-master-credentials.sh"
  install_host_packages
  install_host_scripts
  prepare_central_agent_config
  start_stack
  bash "$ROOT/scripts/install-panel-ssl.sh" || log "UYARI: HTTPS nginx kurulumu atlandi veya basarisiz"
  install_systemd_services
  install_auto_update_timer
  if [[ -x "$ROOT/scripts/verify-client-setup.sh" ]]; then
    bash "$ROOT/scripts/verify-client-setup.sh" || log "UYARI: kurulum dogrulamasinda eksikler var"
  fi
  print_summary
  log "=== setup bitti ==="
}

main "$@"
