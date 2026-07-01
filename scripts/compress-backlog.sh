#!/bin/bash
# Sıkıştırılmamış .dmp yedekleri tek tek gzip ile sıkıştırır (arka plan kuyrugu).
# Kullanim: compress-backlog.sh [yedek_dizini]
set -euo pipefail

YEDEK_DIR="${1:-/yedek/orayedek}"
LOCK_FILE="${YEDEK_DIR}/.compress-backlog.lock"
LOG_FILE="${YEDEK_DIR}/compress-backlog.log"
MIN_FREE_GB="${MIN_FREE_GB:-20}"
WAIT_SEC="${WAIT_SEC:-60}"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

free_gb() {
  df -BG "$YEDEK_DIR" 2>/dev/null | awk 'NR==2 { gsub(/G/,"",$4); print $4 }'
}

wait_for_gzip_idle() {
  while pgrep -x gzip >/dev/null 2>&1; do
    log "Bekleniyor: baska gzip calisiyor (pid $(pgrep -x gzip | tr '\n' ' '))"
    sleep "$WAIT_SEC"
  done
}

needs_compress() {
  local dmp="$1"
  local gz="${dmp}.gz"
  [[ -f "$dmp" ]] || return 1
  if [[ ! -f "$gz" ]]; then
    return 0
  fi
  if pgrep -x gzip >/dev/null 2>&1; then
    return 1
  fi
  if ! gzip -t "$gz" 2>/dev/null; then
    log "Bozuk/yarim gz siliniyor: $(basename "$gz")"
    rm -f "$gz"
    return 0
  fi
  return 1
}

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "Kuyruk zaten calisiyor, cikiliyor."
  exit 0
fi

mkdir -p "$YEDEK_DIR"
touch "$LOG_FILE"

mapfile -t CANDIDATES < <(find "$YEDEK_DIR" -maxdepth 1 -type f -name '*.dmp' -printf '%T@ %p\n' 2>/dev/null | sort -n | awk '{print $2}')

if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
  log "Sikistirilacak .dmp dosyasi yok."
  exit 0
fi

log "Kuyruk basladi: ${#CANDIDATES[@]} aday dosya"

for dmp in "${CANDIDATES[@]}"; do
  if ! needs_compress "$dmp"; then
    log "Atlandi (hazir): $(basename "$dmp")"
    continue
  fi

  wait_for_gzip_idle

  avail="$(free_gb)"
  dmp_gb=$(( ($(stat -c%s "$dmp") + 1024**3 - 1) / 1024**3 ))
  need_gb=$(( dmp_gb / 3 + MIN_FREE_GB ))
  if [[ -n "$avail" && "$avail" -lt "$need_gb" ]]; then
    log "Yetersiz disk (${avail}G bos, en az ${need_gb}G gerekli) — durduruldu: $(basename "$dmp")"
    exit 2
  fi

  log "Sikistiriliyor: $(basename "$dmp") (${dmp_gb}G) -> $(basename "$dmp").gz"
  if nice -n 19 ionice -c 3 gzip -f "$dmp"; then
    gz="${dmp}.gz"
    gz_gb=$(( ($(stat -c%s "$gz") + 1024**3 - 1) / 1024**3 ))
    log "Tamamlandi: $(basename "$gz") (${gz_gb}G), bos alan: $(free_gb)G"
  else
    log "HATA: gzip basarisiz — $(basename "$dmp")"
    exit 1
  fi
done

log "Kuyruk tamamlandi."
