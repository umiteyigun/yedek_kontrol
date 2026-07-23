#!/bin/bash
# =============================================================================
# TRTEK Yedek Scripti v2.1 - coklu Oracle instance
# Kullanim: yedek.sh GUNLUK|HAFTALIK [instance_id]
# instance_id verilmezse instances.list icindeki tum aktif instance'lar sirayla alinir.
# =============================================================================
set -euo pipefail

readonly CONFIG_DIR="/yedek/config"
readonly PARAMS_FILE="${CONFIG_DIR}/yedek-params.sh"
readonly CONFIG_FILE="${CONFIG_DIR}/yedekconfig.sh"
readonly INSTANCES_DIR="${CONFIG_DIR}/instances"
readonly INSTANCES_LIST="${CONFIG_DIR}/instances.list"
DEFAULT_BACKUP_STATUS_FILE="/yedek/orayedek/.backup-status.json"
BACKUP_STATUS_FILE="${BACKUP_STATUS_FILE:-$DEFAULT_BACKUP_STATUS_FILE}"

if [[ -f "${CONFIG_DIR}/backup-status-lib.sh" ]]; then
  # shellcheck source=/dev/null
  source "${CONFIG_DIR}/backup-status-lib.sh"
fi

bs_stage_if_available() {
  if declare -F bs_stage >/dev/null 2>&1; then
    bs_stage "$1" "${INSTANCE_ID:-}" || true
  fi
}

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
die() { log "HATA: $*"; exit 1; }

[[ -f "$PARAMS_FILE" ]] || die "Bulunamadi: $PARAMS_FILE (once panelden ayarlari kaydedin)"
[[ -f "$CONFIG_FILE" ]] || die "Bulunamadi: $CONFIG_FILE (once panelden ayarlari kaydedin)"
[[ -d "$INSTANCES_DIR" ]] || die "Instance dizini yok: $INSTANCES_DIR"

# shellcheck source=/dev/null
source "$PARAMS_FILE"
# shellcheck source=/dev/null
source "$CONFIG_FILE"

yedektipi="${1:-}"
only_instance="${2:-}"
FTP_TARGET="${FTP_TARGET:-primary}"
[[ -n "$yedektipi" ]] || die "Kullanim: yedek.sh GUNLUK|HAFTALIK [instance_id]"
[[ "$yedektipi" == "GUNLUK" || "$yedektipi" == "HAFTALIK" ]] || die "Gecersiz tip: ${yedektipi}"

_ftp_python() {
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    printf '%s\n' python
    return 0
  fi
  return 1
}

sendftpfile() {
  local server="$1" user="$2" pass="$3" ftplog="$4" src="$5" target="$6" remote_dir="${7:-/}"
  local helper="${CONFIG_DIR}/ftp-put.py"
  local py=""
  local rc=1

  remote_dir="${remote_dir:-/}"
  remote_dir="${remote_dir// /}"
  if [[ -z "$remote_dir" ]]; then
    remote_dir="/"
  elif [[ "$remote_dir" != /* ]]; then
    remote_dir="/${remote_dir}"
  fi
  if [[ "$remote_dir" != "/" ]]; then
    remote_dir="${remote_dir%/}"
  fi

  if [[ -z "$ftplog" ]] || ! : >"$ftplog" 2>/dev/null; then
    ftplog="${CONFIG_DIR}/ftp-upload.log"
    : >"$ftplog" 2>/dev/null || ftplog="${CONFIG_DIR}/ftp-upload-$$.log"
    : >"$ftplog" 2>/dev/null || die "FTP log yazilamadi: $ftplog"
  fi

  [[ -f "$helper" ]] || die "FTP helper yok: $helper (install-host-scripts.sh)"
  py="$(_ftp_python)" || die "python/python3 bulunamadi (FTP upload)"

  set +e
  "$py" "$helper" \
    --host "$server" \
    --user "$user" \
    --password "$pass" \
    --local "$src" \
    --remote "$target" \
    --remote-dir "$remote_dir" \
    --log "$ftplog"
  rc=$?
  set -e

  # 0 = size match; 226 is informational only (logged by helper)
  if [[ "$rc" -eq 0 ]]; then
    echo "1"
  else
    echo "0"
  fi
}

_resolve_ftp_credentials() {
  FTP_TARGET="${FTP_TARGET:-primary}"
  ACTIVE_FTP_IP=""
  ACTIVE_FTP_USER=""
  ACTIVE_FTP_PASS=""
  ACTIVE_FTP_DIR="/"
  case "$FTP_TARGET" in
    none|both)
      # both: tek hedef degil; upload_backup_artifact sirayla primary+secondary cozer
      return 1
      ;;
    secondary)
      [[ "${ftp2_upload_enabled:-0}" == "1" ]] || return 1
      ACTIVE_FTP_IP="${localftpip2:-}"
      ACTIVE_FTP_USER="${localftpuser2:-}"
      ACTIVE_FTP_PASS="${localftppass2:-}"
      ACTIVE_FTP_DIR="${localftpdir2:-/}"
      ;;
    *)
      [[ "${ftp_upload_enabled:-0}" == "1" ]] || return 1
      ACTIVE_FTP_IP="${localftpip:-}"
      ACTIVE_FTP_USER="${localftpuser:-}"
      ACTIVE_FTP_PASS="${localftppass:-}"
      ACTIVE_FTP_DIR="${localftpdir:-/}"
      ;;
  esac
  [[ -n "$ACTIVE_FTP_IP" && -n "$ACTIVE_FTP_USER" && -n "$ACTIVE_FTP_PASS" ]]
}

# ACTIVE_FTP_* uzerinden tek hedefe yukle (sendftpfile / ftp-put.py — hang-proof SIZE).
# Globale dokunur: ftp_ok (0/1), total_size, first_name, upload_dosyaadi, filesize
_upload_to_active_ftp() {
  local artifact_path="$1"
  local remote_name="$2"
  local label="${3:-FTP}"
  local ftp_ok=1
  local total_size=0
  local first_name=""

  if [[ "${backup_split_enabled:-0}" == "1" ]]; then
    shopt -s nullglob
    local parts=( "${artifact_path}.part_"* )
    shopt -u nullglob
    if [[ ${#parts[@]} -eq 0 ]]; then
      log "HATA: ${label} split parca yok: ${artifact_path}.part_*"
      return 1
    fi
    for part in "${parts[@]}"; do
      local part_name part_stat psz
      part_name="$(basename "$part")"
      [[ -z "$first_name" ]] && first_name="$part_name"
      part_stat=$(sendftpfile \
        "$ACTIVE_FTP_IP" "$ACTIVE_FTP_USER" "$ACTIVE_FTP_PASS" \
        "$ftplog2" \
        "$part" "$part_name" "$ACTIVE_FTP_DIR")
      [[ "$part_stat" != "1" ]] && ftp_ok=0
      psz=$(stat -c%s "$part" 2>/dev/null || echo 0)
      total_size=$((total_size + psz))
    done
    upload_dosyaadi="${first_name} (+${#parts[@]} parca)"
    filesize=$total_size
    [[ "$ftp_ok" == "1" ]]
    return $?
  fi

  local st
  st=$(sendftpfile \
    "$ACTIVE_FTP_IP" "$ACTIVE_FTP_USER" "$ACTIVE_FTP_PASS" \
    "$ftplog2" \
    "$artifact_path" "$remote_name" "$ACTIVE_FTP_DIR")
  filesize=$(stat -c%s "$artifact_path" 2>/dev/null || echo "-1")
  upload_dosyaadi="$remote_name"
  [[ "$st" == "1" ]]
}

upload_backup_artifact() {
  local artifact_path="$1"
  local remote_name="$2"
  local saved_target="${FTP_TARGET:-primary}"
  local ftp_ok=1

  # Split bir kez (both icin de ayni parcalar iki hedefe gider)
  if [[ "${backup_split_enabled:-0}" == "1" ]]; then
    shopt -s nullglob
    local existing=( "${artifact_path}.part_"* )
    shopt -u nullglob
    if [[ ${#existing[@]} -eq 0 && -f "$artifact_path" ]]; then
      local split_mb="${backup_split_size_mb:-2048}"
      local part_prefix="${artifact_path}.part_"
      log "Buyuk dosya bolunuyor [${INSTANCE_ID}]: ${split_mb}MB parcalar"
      split -b "${split_mb}M" -a 3 -d "$artifact_path" "$part_prefix"
      rm -f "$artifact_path"
      shopt -s nullglob
      existing=( "${artifact_path}.part_"* )
      shopt -u nullglob
      if [[ ${#existing[@]} -eq 0 ]]; then
        die "Split basarisiz: ${artifact_path}"
      fi
    fi
  fi

  if [[ "$saved_target" == "both" ]]; then
    local ok1=0 ok2=0
    FTP_TARGET=primary
    if _resolve_ftp_credentials; then
      log "FTP-1 yukleniyor [${INSTANCE_ID}]: ${ACTIVE_FTP_IP}:${ACTIVE_FTP_DIR:-/} -> ${remote_name}"
      if _upload_to_active_ftp "$artifact_path" "$remote_name" "FTP-1"; then
        ok1=1
        log "FTP-1 OK [${INSTANCE_ID}]"
      else
        log "FTP-1 BASARISIZ [${INSTANCE_ID}]"
      fi
    else
      log "FTP-1 atlandi [${INSTANCE_ID}]: kapali veya kimlik bilgisi eksik"
    fi
    FTP_TARGET=secondary
    if _resolve_ftp_credentials; then
      log "FTP-2 yukleniyor [${INSTANCE_ID}]: ${ACTIVE_FTP_IP}:${ACTIVE_FTP_DIR:-/} -> ${remote_name}"
      if _upload_to_active_ftp "$artifact_path" "$remote_name" "FTP-2"; then
        ok2=1
        log "FTP-2 OK [${INSTANCE_ID}]"
      else
        log "FTP-2 BASARISIZ [${INSTANCE_ID}]"
      fi
    else
      log "FTP-2 atlandi [${INSTANCE_ID}]: kapali veya kimlik bilgisi eksik"
    fi
    FTP_TARGET="$saved_target"
    if [[ "$ok1" == "1" && "$ok2" == "1" ]]; then
      localftpstat="1"
    else
      localftpstat="0"
    fi
    if [[ -z "${filesize:-}" || "$filesize" == "-1" ]]; then
      if [[ -f "$artifact_path" ]]; then
        filesize=$(stat -c%s "$artifact_path" 2>/dev/null || echo "-1")
      else
        filesize="${filesize:--1}"
      fi
    fi
    upload_dosyaadi="${upload_dosyaadi:-$remote_name}"
    log "FTP both ozet [${INSTANCE_ID}]: ftp1=${ok1} ftp2=${ok2} Ftp=${localftpstat}"
    return 0
  fi

  FTP_TARGET="$saved_target"
  if ! _resolve_ftp_credentials; then
    localftpstat=""
    filesize=$(stat -c%s "$artifact_path" 2>/dev/null || echo "-1")
    upload_dosyaadi="$remote_name"
    return 0
  fi

  if _upload_to_active_ftp "$artifact_path" "$remote_name" "FTP"; then
    localftpstat="1"
  else
    localftpstat="0"
  fi
}

notify_backup() {
  local api_url="http://127.0.0.1:${core_port:-8090}/api/YedekYonetimi/YedekBildirimi"
  local resp_file="${directorydizini}.api-response"
  local disk1 disk2 disk3 disip http_code
  local disk_report_script="${CONFIG_DIR}/disk-report.sh"

  if [[ -x "$disk_report_script" ]]; then
    # shellcheck disable=SC2046
    eval "$("$disk_report_script" "${directorydizini}")"
    disk1=${DiskAlani1:-${disk1:-0%}}
    disk2=${DiskAlani2:-${disk2:-0}}
    disk3=${DiskAlani3:-${disk3:-0}}
  else
    disk1=$(df -h / 2>/dev/null | awk 'NR==2 {print $5}')
    disk1=${disk1:-0%}
    disk2=0
    disk3=0
  fi

  disip=$(curl -sf --max-time 8 ifconfig.co 2>/dev/null || true)
  if [[ -z "$disip" ]]; then
    disip=$(curl -sf --max-time 5 https://api.ipify.org 2>/dev/null || true)
  fi
  if [[ -z "$disip" ]]; then
    disip=$(hostname -I 2>/dev/null | awk '{print $1}')
  fi
  disip=${disip:-0.0.0.0}

  log "API bildirimi [${INSTANCE_ID}]: ${api_url}"
  http_code=$(curl -sS -G "$api_url" \
    -w "%{http_code}" \
    -o "$resp_file" \
    --connect-timeout 10 \
    --max-time 30 \
    --data-urlencode "GuidKey=${guid_key}" \
    --data-urlencode "YedekKodu=${yedek_kodu}" \
    --data-urlencode "KurumNo=${kurumkodu}" \
    --data-urlencode "Hastane=${hastane}" \
    --data-urlencode "Il=${il}" \
    --data-urlencode "Hostname=${hostname}" \
    --data-urlencode "InstanceId=${INSTANCE_ID}" \
    --data-urlencode "OracleSid=${ORACLE_SID}" \
    --data-urlencode "Tarih=${trtektarih}" \
    --data-urlencode "DisIp=${disip}" \
    --data-urlencode "DiskAlani1=${disk1}" \
    --data-urlencode "DiskAlani2=${disk2}" \
    --data-urlencode "DiskAlani3=${disk3}" \
    --data-urlencode "YedekBoyutu=${filesize}" \
    --data-urlencode "Ftp=${localftpstat}" \
    --data-urlencode "Mail=${mail_notify:-1}" \
    --data-urlencode "YedekTipi=${yedektipi}" \
    --data-urlencode "DosyaAdi=${upload_dosyaadi:-${gzipdosyaadi}}" \
  ) || http_code="000"

  if [[ "$http_code" == "200" ]]; then
    log "API bildirimi OK [${INSTANCE_ID}] (HTTP 200)"
  else
    log "UYARI: API bildirimi basarisiz [${INSTANCE_ID}] (HTTP ${http_code})"
  fi

  {
    echo "=== API Bildirimi $(date) instance=${INSTANCE_ID} ==="
    echo "HTTP: ${http_code}"
    cat "$resp_file" 2>/dev/null || true
  } >> "${directorydizini}${logdosyaadi}" 2>/dev/null || true
  rm -f "$resp_file"
}

backup_current_instance() {
  [[ -n "${hastane:-}" ]] || die "Instance ${INSTANCE_ID}: hastane adi bos"
  [[ -x "${ORACLE_HOME}/bin/expdp" ]] || die "expdp bulunamadi: ${ORACLE_HOME}/bin/expdp"

  export ORACLE_SID
  mkdir -p "$directorydizini"

  backup_protect_mode="${backup_protect_mode:-gzip}"
  backup_protect_pass="${backup_protect_pass:-}"
  backup_split_enabled="${backup_split_enabled:-0}"
  backup_split_size_mb="${backup_split_size_mb:-2048}"

  tarih=$(date +%Y%m%d%H)
  trtektarih=$(date +%Y%m%d)
  yedekleme="${yedektipi}YEDEK"
  kurum_slug="${backup_prefix:-${label:-${hastane:-${INSTANCE_ID}}}}"
  kurumadi="${kurum_slug}${yedekleme}${tarih}"
  dosyaadi="${kurumadi}."
  dmpdosyaadi="${dosyaadi}dmp"
  logdosyaadi="${dosyaadi}log"
  gzipdosyaadi="${dosyaadi}dmp.gz"
  zipdosyaadi="${kurumadi}.zip"
  uploaddosyaadi=""
  upload_dosyaadi=""
  localftpstat="0"
  filesize="-1"

  if [[ "$backup_protect_mode" == "oracle" || "$backup_protect_mode" == "zip" ]]; then
    [[ -n "$backup_protect_pass" ]] || die "Instance ${INSTANCE_ID}: yedek koruma sifresi bos (${backup_protect_mode})"
  fi
  if [[ "$backup_protect_mode" == "zip" ]]; then
    command -v zip >/dev/null 2>&1 || die "zip komutu yok (setup.sh ile kurun)"
  fi

  log "Yedek basliyor [${INSTANCE_ID}]: sid=${ORACLE_SID} tip=${yedektipi} schema=${schemas} kurum=${hastane} mod=${backup_protect_mode} (SYSDBA)"
  bs_stage_if_available exporting

  expdp_common=(
    "$ORACLE_HOME/bin/expdp" userid='"/ as sysdba"'
    directory="$directory" dumpfile="$dmpdosyaadi" logfile="$logdosyaadi"
    job_name="${yedekleme}${tarih}"
  )

  run_expdp_allow_warnings() {
    local expdp_exit=0
    set +e
    "$@"
    expdp_exit=$?
    set -e
    if [[ ! -f "${directorydizini}${dmpdosyaadi}" ]]; then
      die "expdp basarisiz (exit=${expdp_exit}), dmp yok: ${directorydizini}${dmpdosyaadi}"
    fi
    if [[ $expdp_exit -ne 0 ]]; then
      log "UYARI: expdp uyarilarla tamamlandi (exit=${expdp_exit}), sikistirmaya devam [${INSTANCE_ID}]"
    fi
  }

  case "$backup_protect_mode" in
    oracle)
      if [[ "$yedektipi" == "GUNLUK" ]]; then
        run_expdp_allow_warnings "${expdp_common[@]}" schemas="$schemas" \
          compression=all encryption=all encryption_mode=password \
          encryption_password="$backup_protect_pass"
      else
        run_expdp_allow_warnings "${expdp_common[@]}" full=y flashback_time=systimestamp \
          compression=all encryption=all encryption_mode=password \
          encryption_password="$backup_protect_pass"
      fi
      ;;
    zip)
      if [[ "$yedektipi" == "GUNLUK" ]]; then
        run_expdp_allow_warnings "${expdp_common[@]}" schemas="$schemas" compression=all
      else
        run_expdp_allow_warnings "${expdp_common[@]}" full=y flashback_time=systimestamp compression=all
      fi
      ;;
    gzip|*)
      if [[ "$yedektipi" == "GUNLUK" ]]; then
        run_expdp_allow_warnings "${expdp_common[@]}" schemas="$schemas"
      else
        run_expdp_allow_warnings "${expdp_common[@]}" full=y flashback_time=systimestamp
      fi
      ;;
  esac

  if [[ -f "${directorydizini}.lst" ]]; then
    cat "${directorydizini}.lst" >> "${directorydizini}${logdosyaadi}"
    rm -f "${directorydizini}.lst"
  fi
  {
    echo "=== Sistem bilgisi $(date) instance=${INSTANCE_ID} sid=${ORACLE_SID} mod=${backup_protect_mode} ==="
    df -h
    free -m 2>/dev/null || free
  } >> "${directorydizini}${logdosyaadi}" 2>&1

  artifact_path=""
  bs_stage_if_available compressing
  case "$backup_protect_mode" in
    oracle)
      log "Oracle sifreli yedek sikistiriliyor [${INSTANCE_ID}]: ${directorydizini}${dmpdosyaadi}"
      gzip -f "${directorydizini}${dmpdosyaadi}"
      artifact_path="${directorydizini}${gzipdosyaadi}"
      uploaddosyaadi="$gzipdosyaadi"
      log "Oracle sifreli yedek hazir [${INSTANCE_ID}]: ${artifact_path}"
      ;;
    zip)
      log "Zip sifreli arsiv olusturuluyor [${INSTANCE_ID}]"
      zip -j -P "$backup_protect_pass" "${directorydizini}${zipdosyaadi}" "${directorydizini}${dmpdosyaadi}"
      rm -f "${directorydizini}${dmpdosyaadi}"
      artifact_path="${directorydizini}${zipdosyaadi}"
      uploaddosyaadi="$zipdosyaadi"
      ;;
    gzip|*)
      log "Sikistiriliyor [${INSTANCE_ID}]: ${directorydizini}${dmpdosyaadi}"
      gzip -f "${directorydizini}${dmpdosyaadi}"
      artifact_path="${directorydizini}${gzipdosyaadi}"
      uploaddosyaadi="$gzipdosyaadi"
      ;;
  esac

  [[ -f "$artifact_path" ]] || die "Yedek dosyasi olusmadi: ${artifact_path}"

  sqlplus -s /nolog <<EOF >> "${directorydizini}${logdosyaadi}" 2>&1
WHENEVER SQLERROR EXIT SQL.SQLCODE
CONNECT / AS SYSDBA
SET PAGESIZE 100
SELECT username, expiry_date FROM dba_users
 WHERE username IN ('AKILU','SYSTEM')
   AND expiry_date < SYSDATE + 60
 ORDER BY username;
EXIT;
EOF

  if [[ "${FTP_TARGET:-primary}" == "both" ]]; then
    bs_stage_if_available ftp_upload
    log "FTP yukleniyor [${INSTANCE_ID}]: hedef=both (FTP-1 sonra FTP-2) -> ${uploaddosyaadi}"
    upload_backup_artifact "$artifact_path" "$uploaddosyaadi"
  elif _resolve_ftp_credentials; then
    bs_stage_if_available ftp_upload
    log "FTP yukleniyor [${INSTANCE_ID}]: hedef=${FTP_TARGET} ${ACTIVE_FTP_IP}:${ACTIVE_FTP_DIR:-/} -> ${uploaddosyaadi}"
    upload_backup_artifact "$artifact_path" "$uploaddosyaadi"
  else
    localftpstat=""
    filesize=$(stat -c%s "$artifact_path" 2>/dev/null || stat -f%z "$artifact_path" 2>/dev/null || echo "-1")
    upload_dosyaadi="$uploaddosyaadi"
    log "FTP atlandi [${INSTANCE_ID}] hedef=${FTP_TARGET}: ${uploaddosyaadi}"
  fi
  gzipdosyaadi="${upload_dosyaadi:-$uploaddosyaadi}"

  bs_stage_if_available notifying
  notify_backup
  log "Yedek tamamlandi [${INSTANCE_ID}]: ${gzipdosyaadi} (${filesize} byte) FTP=${localftpstat} mod=${backup_protect_mode}"
}

run_instance_file() {
  local instance_file="$1"
  # shellcheck source=/dev/null
  source "$instance_file"
  BACKUP_STATUS_FILE="${directorydizini%/}/.backup-status.json"
  export BACKUP_STATUS_FILE
  backup_current_instance
}

if [[ -n "$only_instance" ]]; then
  instance_file="${INSTANCES_DIR}/${only_instance}.sh"
  [[ -f "$instance_file" ]] || die "Instance bulunamadi: ${only_instance}"
  run_instance_file "$instance_file"
  exit 0
fi

[[ -f "$INSTANCES_LIST" ]] || die "instances.list bulunamadi"
mapfile -t INSTANCE_IDS < "$INSTANCES_LIST"
[[ ${#INSTANCE_IDS[@]} -gt 0 ]] || die "Aktif instance yok (panel -> Ayarlar -> Instance Ekle)"

for instance_id in "${INSTANCE_IDS[@]}"; do
  [[ -n "$instance_id" ]] || continue
  instance_file="${INSTANCES_DIR}/${instance_id}.sh"
  [[ -f "$instance_file" ]] || die "Instance scripti yok: ${instance_id}"
  run_instance_file "$instance_file"
done

exit 0
