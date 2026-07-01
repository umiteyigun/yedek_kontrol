#!/bin/bash
# Client kurulum bütünlük kontrolü — eksik dosya/servis raporu.
# Kullanım: bash scripts/verify-client-setup.sh  (root önerilir)
set -euo pipefail

ROOT="${YEDEK_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ERR=0
WARN=0

ok()   { echo "  OK   $*"; }
miss() { echo "  EKSIK $*"; ERR=1; }
warn() { echo "  UYARI $*"; WARN=1; }

echo "=== Yedek client kurulum kontrolu ==="
echo "ROOT=$ROOT"
echo

# --- systemd ---
echo "[systemd]"
for unit in yedek-docker yedek-backup-watcher docker; do
  if systemctl is-enabled "$unit" &>/dev/null && systemctl is-active "$unit" &>/dev/null; then
    ok "$unit (active, enabled)"
  else
    miss "$unit (active=$(systemctl is-active $unit 2>/dev/null), enabled=$(systemctl is-enabled $unit 2>/dev/null))"
  fi
done

if [[ -d "$ROOT/.git" ]]; then
  if systemctl is-enabled yedek-auto-update.timer &>/dev/null; then
    ok "yedek-auto-update.timer (enabled)"
  else
    miss "yedek-auto-update.timer"
  fi
else
  warn "git repo yok — auto-update timer beklenmiyor"
fi

if command -v nginx >/dev/null 2>&1 && systemctl is-active nginx &>/dev/null; then
  ok "nginx (active)"
else
  miss "nginx"
fi
echo

# --- /usr/bin ---
echo "[/usr/bin]"
for f in yedek.sh yedek2.sh yedekconfig.sh yedekconfig2.sh rman.sh; do
  [[ -e "/usr/bin/$f" ]] && ok "/usr/bin/$f" || miss "/usr/bin/$f"
done
echo

# --- /yedek/config host scriptleri ---
echo "[/yedek/config]"
HOST_SCRIPTS=(
  run-backup.sh backup-watcher.sh oracle-probe.sh oracle-schemas.sh
  oracle-rman-probe.sh oracle-stats.sh oracle-tablespaces.sh run-rman.sh
  host-info.sh host-timezone.sh disk-check-backup.sh disk-report.sh
  terminal-shell.sh yedek-web-terminal-profile.sh yedek-web-terminal-guard.sh
  install-panel-ssl.sh install-host-scripts.sh
  yedekconfig.sh
)
for f in "${HOST_SCRIPTS[@]}"; do
  [[ -f "/yedek/config/$f" ]] && ok "$f" || miss "/yedek/config/$f"
done

for f in auto-update.env auto-update.local.sh central-agent.env; do
  if [[ -f "/yedek/config/$f" ]]; then
    ok "$f"
  elif [[ "$f" == auto-update.env && ! -d "$ROOT/.git" ]]; then
    warn "$f (git kurulumu degil)"
  elif [[ "$f" == auto-update.local.sh ]]; then
    warn "$f (opsiyonel, koruma listesi)"
  else
    miss "/yedek/config/$f"
  fi
done

[[ -d /yedek/config/agent-state ]] && ok "agent-state/" || warn "agent-state/ (agent kayit oncesi bos olabilir)"
[[ -f /etc/profile.d/99-yedek-web-terminal.sh ]] && ok "profile.d/99-yedek-web-terminal.sh" || miss "profile.d/99-yedek-web-terminal.sh"
echo

# --- terminal guard ---
echo "[terminal-bin]"
[[ -x /yedek/config/terminal-bin/yedek-terminal-blocked ]] && ok "yedek-terminal-blocked" || miss "terminal-bin/yedek-terminal-blocked"
echo

# --- SSL / nginx ---
echo "[ssl/nginx]"
[[ -f /yedek/ssl/panel.crt && -f /yedek/ssl/panel.key ]] && ok "/yedek/ssl sertifika" || miss "/yedek/ssl panel.crt/key"
[[ -f /etc/nginx/conf.d/yedek-panel.conf ]] && ok "nginx yedek-panel.conf" || miss "/etc/nginx/conf.d/yedek-panel.conf"
echo

# --- repo scriptleri ---
echo "[repo scripts]"
for f in yedek-auto-update.sh yedek-local-deploy.sh yedek-docker-ctl.sh install-host-scripts.sh setup.sh; do
  [[ -x "$ROOT/scripts/$f" || -f "$ROOT/$f" ]] || { miss "$ROOT/scripts/$f"; continue; }
  if [[ "$f" == setup.sh ]]; then
    [[ -f "$ROOT/setup.sh" ]] && ok "setup.sh" || miss "setup.sh"
  else
    [[ -x "$ROOT/scripts/$f" ]] && ok "scripts/$f" || miss "scripts/$f (calistirilabilir degil)"
  fi
done

for f in .env config/settings.json docker-compose.yml; do
  [[ -f "$ROOT/$f" ]] && ok "$f" || miss "$ROOT/$f"
done

if [[ -d "$ROOT/.git" ]]; then
  grep -q "^YEDEK_ROOT=" /yedek/config/auto-update.env 2>/dev/null \
    && ok "auto-update.env YEDEK_ROOT" \
    || miss "auto-update.env icinde YEDEK_ROOT yok"
fi
echo

# --- docker ---
echo "[docker]"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^yedek-core$'; then
  ok "yedek-core container"
else
  miss "yedek-core container calismiyor"
fi

if [[ -f /yedek/config/central-agent.env ]]; then
  # shellcheck source=/dev/null
  source /yedek/config/central-agent.env
  if [[ -n "${ORG_ENROLLMENT_CODE:-}" ]]; then
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -q '^yedek-central-agent$'; then
      ok "yedek-central-agent container"
    else
      miss "yedek-central-agent (ORG_ENROLLMENT_CODE dolu ama container yok)"
    fi
  else
    warn "central-agent atlandi (ORG_ENROLLMENT_CODE bos)"
  fi
fi
echo

# --- bagimliliklar ---
echo "[bagimliliklar]"
for c in docker git flock nginx python zip unzip; do
  command -v "$c" >/dev/null 2>&1 && ok "$c" || miss "komut: $c"
done
command -v openssl >/dev/null 2>&1 && ok "openssl" || miss "openssl"
echo

echo "=== Sonuc ==="
if [[ "$ERR" -eq 0 ]]; then
  echo "Tum zorunlu kontroller gecti.${WARN:+ ($WARN uyari)}"
  exit 0
fi
echo "EKSIKLER VAR — bash setup.sh ile yeniden kurulum veya eksikleri elle tamamlayin."
exit 1
