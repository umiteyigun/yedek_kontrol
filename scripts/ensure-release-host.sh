#!/bin/bash
# RHEL6 / fake-systemctl / cron hostlari icin kalici release + watcher dongusu.
# systemd timer yoksa cron.d yazar; cift flock KULLANMAZ; watcher'i nohup ile tutar.
set -euo pipefail

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[$(ts)] ensure-release-host: $*"; }

ENVF=/yedek/config/release-update.env
CRON_REL=/etc/cron.d/yedek-release-update
CRON_WATCH=/etc/cron.d/yedek-backup-watcher
UPD=/yedek/config/release-updater.sh
WATCH=/yedek/config/backup-watcher.sh
LOG_REL=/var/log/yedek-release-update.log
LOG_WATCH=/yedek/orayedek/backup-watcher.log

mkdir -p /yedek/config /yedek/orayedek /var/log /opt/yedek_kontrol/scripts
touch "$LOG_REL" 2>/dev/null || true
chmod 640 "$LOG_REL" 2>/dev/null || true

# --- env: latest + Hub manifesto ---
if [[ -f "$ENVF" ]]; then
  cp -a "$ENVF" "${ENVF}.bak.ensure.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
  for kv in \
    "RELEASE_UPDATER_ENABLED=1" \
    "RELEASE_TRACK=latest" \
    "RELEASE_UNLOCK_LATEST=1" \
    "RELEASE_SKIP_PULL=0" \
    "RELEASE_MANIFEST_URL=https://centos.trtekyazilim.com:8444/release/latest.env" \
    "HUB_MANIFEST_URL=https://centos.trtekyazilim.com:8444/release/latest.env"
  do
    k=${kv%%=*}; v=${kv#*=}
    if grep -q "^${k}=" "$ENVF" 2>/dev/null; then
      sed -i "s|^${k}=.*|${k}=${v}|" "$ENVF"
    else
      echo "${k}=${v}" >>"$ENVF"
    fi
  done
  log "release-update.env TRACK=latest + Hub :8444"
fi

# --- release cron ---
# Deploy/force sirasinda cron yazma: ortadaki compose ile cakisip Conflict/rollback yapar.
# Pause dosyasini asla /etc/cron.d icinde birakma (bazı sistemler .paused dosyasini da okur).
rm -f /etc/cron.d/yedek-release-update.paused /etc/cron.d/yedek-release-update.bak 2>/dev/null || true
if [[ "${YEDEK_RELEASE_DEPLOYING:-0}" == "1" && "${YEDEK_RELEASE_ALLOW_CRON:-0}" != "1" ]]; then
  if [[ -f "$CRON_REL" ]]; then
    mv -f "$CRON_REL" /var/tmp/yedek-release-update.cron.paused 2>/dev/null || rm -f "$CRON_REL"
  fi
  log "deploying — cron rewrite skip (paused to /var/tmp)"
else
  cat >"$CRON_REL" <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
*/2 * * * * root /yedek/config/release-updater.sh >>/var/log/yedek-release-update.log 2>&1
EOF
  chmod 644 "$CRON_REL"
  log "cron.d/yedek-release-update yazildi (flock yok)"
fi
# Cron kullanan hostlarda systemd timer cift ateslemasin
if command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now yedek-release-update.timer 2>/dev/null || true
fi

# --- watcher keep-alive cron (process yoksa baslat) ---
cat >"$CRON_WATCH" <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
*/5 * * * * root pgrep -f '/yedek/config/backup-watcher\.sh' >/dev/null 2>&1 || nohup /yedek/config/backup-watcher.sh >>/yedek/orayedek/backup-watcher.log 2>&1 9>&- &
EOF
chmod 644 "$CRON_WATCH"
log "cron.d/yedek-backup-watcher keep-alive yazildi"

# --- watcher simdi ---
if [[ -x "$WATCH" ]]; then
  if ! pgrep -f '/yedek/config/backup-watcher\.sh' >/dev/null 2>&1; then
    nohup "$WATCH" >>"$LOG_WATCH" 2>&1 9>&- &
    log "backup-watcher baslatildi pid=$!"
  else
    log "backup-watcher zaten calisiyor"
  fi
else
  log "UYARI: $WATCH yok"
fi

# Timer'i ASLA otomatik start etme (cron ile cift ates). Watcher service istege bagli.
if [[ -d /run/systemd/system ]] && command -v systemctl >/dev/null 2>&1; then
  systemctl disable --now yedek-release-update.timer 2>/dev/null || true
  systemctl start yedek-backup-watcher.service 2>/dev/null || true
fi

log "hazir"
exit 0
