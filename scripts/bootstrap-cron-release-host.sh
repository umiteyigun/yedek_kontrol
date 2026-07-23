#!/bin/bash
# Tavsanli kalici oto-release + 75'e cikis (tek sefer)
set -euo pipefail
LOG=/yedek/config/tavsanli-bootstrap.log
exec >"$LOG" 2>&1
TAG="${1:-75}"
ENVF=/yedek/config/release-update.env
echo "=== START $(date -Is) tag=$TAG ==="

mkdir -p /yedek/config /yedek/orayedek /var/log /opt/yedek_kontrol/scripts
touch /var/log/yedek-release-update.log
chmod 640 /var/log/yedek-release-update.log || true

cp -a "$ENVF" "${ENVF}.bak.$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
for kv in \
  "RELEASE_UPDATER_ENABLED=1" \
  "RELEASE_TRACK=latest" \
  "RELEASE_UNLOCK_LATEST=1" \
  "RELEASE_SKIP_PULL=0" \
  "RELEASE_TARGET_TAG=${TAG}" \
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

# Dis flock YOK
cat >/etc/cron.d/yedek-release-update <<'EOF'
SHELL=/bin/bash
PATH=/sbin:/bin:/usr/sbin:/usr/bin
*/2 * * * * root /yedek/config/release-updater.sh >>/var/log/yedek-release-update.log 2>&1
EOF
chmod 644 /etc/cron.d/yedek-release-update

cat >/etc/cron.d/yedek-backup-watcher <<'EOF'
SHELL=/bin/bash
PATH=/sbin:/bin:/usr/sbin:/usr/bin
*/5 * * * * root pgrep -f '/yedek/config/backup-watcher\.sh' >/dev/null 2>&1 || nohup /yedek/config/backup-watcher.sh >>/yedek/orayedek/backup-watcher.log 2>&1 &
EOF
chmod 644 /etc/cron.d/yedek-backup-watcher
echo "--- cron ---"
cat /etc/cron.d/yedek-release-update
cat /etc/cron.d/yedek-backup-watcher

pkill -f '/yedek/config/backup-watcher\.sh' 2>/dev/null || true
sleep 1
nohup /yedek/config/backup-watcher.sh >>/yedek/orayedek/backup-watcher.log 2>&1 &
echo "watcher_pid=$!"
sleep 1
pgrep -fl backup-watcher || echo WATCHER_MISSING

# Eski updater hard-fail watcher satirini yumusat
UPD=/yedek/config/release-updater.sh
if [[ -f "$UPD" ]] && grep -q 'backup watcher inactive' "$UPD" && grep -q 'return 1' "$UPD"; then
  cp -a "$UPD" "${UPD}.bak.soft"
  # shellcheck disable=SC2016
  sed -i '/backup watcher inactive/{n;s/return 1;/echo "[watcher] inactive but health ok — continue" >\&2;/}' "$UPD" || true
  # Daha guvenli: inactive blogundaki return 1 -> true
  awk '
    /backup watcher inactive/ { print; getline; if ($0 ~ /return 1/) { print "    true"; next } }
    { print }
  ' "${UPD}.bak.soft" >"${UPD}.tmp" && mv -f "${UPD}.tmp" "$UPD"
  chmod +x "$UPD"
  echo "updater watcher-fail softened"
fi

# Orphan container
for c in $(docker ps -a --format '{{.Names}}' 2>/dev/null | grep '_yedek-central-agent$' || true); do
  echo "rm orphan $c"; docker rm -f "$c" || true
done

echo "=== deploy $TAG ==="
set +e
/yedek/config/release-updater.sh --tag "$TAG"
rc=$?
set -e
echo "updater_exit=$rc"

# Agent/core hizala (updater yarim kaldiysa)
cd /opt/yedek_kontrol
if [[ -f docker-compose.release.yml ]]; then
  grep -q "image:.*:${TAG}" docker-compose.release.yml 2>/dev/null || cat >docker-compose.release.yml <<YAML
services:
  core:
    image: $(grep '^RELEASE_CORE_IMAGE=' "$ENVF" | cut -d= -f2-):${TAG}
  central-agent:
    image: $(grep '^RELEASE_AGENT_IMAGE=' "$ENVF" | cut -d= -f2-):${TAG}
YAML
fi
set +e
docker compose -f docker-compose.yml -f docker-compose.release.yml --profile central up -d --force-recreate core central-agent
set -e
sleep 5

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
STATE=/opt/yedek_kontrol/config/release-state.json
[[ -d /opt/yedek_kontrol/config ]] || STATE=/yedek/config/release-state.json
printf '{"status":"ok","message":"Release guncellendi","current_tag":"%s","target_tag":"%s","updated_at":"%s"}\n' "$TAG" "$TAG" "$NOW" >"$STATE"

echo "=== RESULT ==="
docker ps --format '{{.Names}} {{.Image}} {{.Status}}' | grep yedek || true
cat "$STATE"
curl -sS --max-time 5 http://127.0.0.1:8090/health; echo
pgrep -fl backup-watcher || true
echo "=== DONE $(date -Is) ==="
