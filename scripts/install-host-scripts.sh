#!/bin/bash
# Repo'daki host scriptlerini /yedek/config ve /usr/bin altina kurar.
# setup.sh ve yedek-local-deploy.sh tarafindan cagrilir.
set -euo pipefail

if [[ -n "${YEDEK_ROOT:-}" && -f "${YEDEK_ROOT}/scripts/yedek.sh" ]]; then
  ROOT="$YEDEK_ROOT"
else
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if [[ ! -f "$ROOT/scripts/yedek.sh" && -f /opt/yedek_kontrol/scripts/yedek.sh ]]; then
    ROOT="/opt/yedek_kontrol"
  fi
fi

if [[ ! -f "$ROOT/scripts/yedek.sh" ]]; then
  echo "[host-scripts] HATA: repo bulunamadi (ROOT=$ROOT)" >&2
  exit 1
fi

log() { echo "[host-scripts] $*"; }

mkdir -p /yedek/config /yedek/config/terminal-bin

install -m 755 "$ROOT/scripts/yedek.sh" /usr/bin/yedek.sh
ln -sfn /usr/bin/yedek.sh /usr/bin/yedek2.sh

install -m 755 "$ROOT/scripts/ftp-put.py" /yedek/config/ftp-put.py
install -m 755 "$ROOT/scripts/run-backup.sh" /yedek/config/run-backup.sh
install -m 755 "$ROOT/scripts/backup-watcher.sh" /yedek/config/backup-watcher.sh
install -m 755 "$ROOT/scripts/oracle-probe.sh" /yedek/config/oracle-probe.sh
install -m 755 "$ROOT/scripts/oracle-schemas.sh" /yedek/config/oracle-schemas.sh
install -m 755 "$ROOT/scripts/oracle-rman-probe.sh" /yedek/config/oracle-rman-probe.sh
install -m 755 "$ROOT/scripts/oracle-stats.sh" /yedek/config/oracle-stats.sh
install -m 755 "$ROOT/scripts/oracle-tablespaces.sh" /yedek/config/oracle-tablespaces.sh
install -m 755 "$ROOT/scripts/rman.sh" /usr/bin/rman.sh
install -m 755 "$ROOT/scripts/run-rman.sh" /yedek/config/run-rman.sh
install -m 755 "$ROOT/scripts/host-info.sh" /yedek/config/host-info.sh
install -m 755 "$ROOT/scripts/host-timezone.sh" /yedek/config/host-timezone.sh
install -m 755 "$ROOT/scripts/disk-check-backup.sh" /yedek/config/disk-check-backup.sh
install -m 755 "$ROOT/scripts/disk-report.sh" /yedek/config/disk-report.sh
install -m 755 "$ROOT/scripts/backup-status-lib.sh" /yedek/config/backup-status-lib.sh
install -m 755 "$ROOT/scripts/terminal-shell.sh" /yedek/config/terminal-shell.sh
install -m 644 "$ROOT/scripts/yedek-web-terminal-profile.sh" /yedek/config/yedek-web-terminal-profile.sh
install -m 644 "$ROOT/scripts/yedek-web-terminal-guard.sh" /yedek/config/yedek-web-terminal-guard.sh
install -m 644 "$ROOT/scripts/99-yedek-web-terminal.sh" /etc/profile.d/99-yedek-web-terminal.sh
install -m 755 "$ROOT/scripts/terminal-bin/yedek-terminal-blocked" /yedek/config/terminal-bin/yedek-terminal-blocked
for _cmd in passwd chpasswd chage vipw vigr htpasswd; do
  ln -sfn /yedek/config/terminal-bin/yedek-terminal-blocked "/yedek/config/terminal-bin/${_cmd}"
done
install -m 755 "$ROOT/scripts/install-panel-ssl.sh" /yedek/config/install-panel-ssl.sh
install -m 755 "$ROOT/scripts/ensure-panel-ssl-access.sh" /yedek/config/ensure-panel-ssl-access.sh

# nginx ExecStartPre drop-in (systemd)
if command -v systemctl >/dev/null 2>&1 && [[ -d /etc/systemd/system || -d /usr/lib/systemd/system ]]; then
  mkdir -p /etc/systemd/system/nginx.service.d
  if [[ -f "$ROOT/scripts/nginx-yedek-ssl.service.d.conf" ]]; then
    install -m 644 "$ROOT/scripts/nginx-yedek-ssl.service.d.conf" /etc/systemd/system/nginx.service.d/90-yedek-ssl.conf
  else
    cat >/etc/systemd/system/nginx.service.d/90-yedek-ssl.conf <<'UNIT'
[Service]
ExecStartPre=/yedek/config/ensure-panel-ssl-access.sh
UNIT
  fi
  systemctl daemon-reload 2>/dev/null || true
fi
# hemen bir kez uygula (reboot beklemeden)
bash /yedek/config/ensure-panel-ssl-access.sh 2>/dev/null || true

ln -sfn /yedek/config/yedekconfig.sh /usr/bin/yedekconfig.sh 2>/dev/null || true
ln -sfn /yedek/config/yedekconfig.sh /usr/bin/yedekconfig2.sh 2>/dev/null || true

install -m 755 "$ROOT/scripts/install-host-scripts.sh" /yedek/config/install-host-scripts.sh

# release-updater hostta kalici kopya (image deploy sonrasi da guncellenir)
mkdir -p /opt/yedek_kontrol/scripts
if [[ -f "$ROOT/scripts/release-updater.sh" ]]; then
  install -m 755 "$ROOT/scripts/release-updater.sh" /opt/yedek_kontrol/scripts/release-updater.sh
  install -m 755 "$ROOT/scripts/release-updater.sh" /yedek/config/release-updater.sh
fi

# backup-watcher bash process eski scripti bellekte tutar; yeniyi yukle
if command -v systemctl >/dev/null 2>&1; then
  systemctl restart yedek-backup-watcher.service 2>/dev/null || true
fi
# systemd yok / fake systemctl / unit yoksa process ile garanti et
if ! pgrep -f '/yedek/config/backup-watcher\.sh' >/dev/null 2>&1; then
  mkdir -p /yedek/orayedek
  nohup /yedek/config/backup-watcher.sh >>/yedek/orayedek/backup-watcher.log 2>&1 &
  log "backup-watcher nohup ile baslatildi"
else
  # eski process'i yenile
  pkill -f '/yedek/config/backup-watcher\.sh' 2>/dev/null || true
  sleep 1
  nohup /yedek/config/backup-watcher.sh >>/yedek/orayedek/backup-watcher.log 2>&1 &
fi

# RHEL6 vb: release cron (dis flock YOK — script kendi kilitini alir)
if [[ ! -d /run/systemd/system ]] && [[ ! -f /etc/cron.d/yedek-release-update ]]; then
  printf '%s\n' \
    'SHELL=/bin/bash' \
    'PATH=/sbin:/bin:/usr/sbin:/usr/bin' \
    '*/2 * * * * root /yedek/config/release-updater.sh >>/var/log/yedek-release-update.log 2>&1' \
    >/etc/cron.d/yedek-release-update
  chmod 644 /etc/cron.d/yedek-release-update
  log "cron.d/yedek-release-update yazildi"
fi

log "kuruldu ($(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo local))"
