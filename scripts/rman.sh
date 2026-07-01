#!/bin/bash
# =============================================================================
# TRTEK RMAN Yedek Scripti
# Kullanim: rman.sh RMAN_FULL|RMAN_INCR|RMAN_FULL_MANUAL [instance_id]
# Klasor yapisi:
#   <rman_dest>/full/<kurum>RMANFULLYYYYMMDDHH>/
#   <rman_dest>/fark/<kurum>RMANFARKYYYYMMDDHH>/
#   <rman_dest>/full/manuel/<kurum>RMANMANUELFULLYYYYMMDDHH>/
# =============================================================================
set -euo pipefail

readonly CONFIG_DIR="/yedek/config"
readonly CONFIG_FILE="${CONFIG_DIR}/yedekconfig.sh"
readonly INSTANCES_DIR="${CONFIG_DIR}/instances"
readonly INSTANCES_LIST="${CONFIG_DIR}/instances.list"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

COLD_BACKUP_ACTIVE=0
COLD_MARKER=""

ensure_database_open() {
  local oh="${ORACLE_HOME:-}"
  [[ -n "$oh" && -x "${oh}/bin/sqlplus" ]] || return 0
  log "Guvenlik: veritabani OPEN durumuna getiriliyor [${INSTANCE_ID:-?}] sid=${ORACLE_SID:-?}"
  "${oh}/bin/sqlplus" -s / as sysdba <<'SQL' 2>&1 | while read -r line; do log "DB-OPEN: $line"; done
whenever sqlerror continue
startup;
alter database open;
exit;
SQL
}

cold_backup_cleanup() {
  if [[ "${COLD_BACKUP_ACTIVE}" != "1" ]]; then
    return 0
  fi
  ensure_database_open
  if [[ -n "${COLD_MARKER}" && -f "${COLD_MARKER}" ]]; then
    rm -f "${COLD_MARKER}"
  fi
  COLD_BACKUP_ACTIVE=0
}

die() {
  log "HATA: $*"
  cold_backup_cleanup
  exit 1
}

trap cold_backup_cleanup EXIT

rman_kind="${1:-}"
only_instance="${2:-}"
[[ -n "$rman_kind" ]] || die "Kullanim: rman.sh RMAN_FULL|RMAN_INCR|RMAN_FULL_MANUAL [instance_id]"
[[ "$rman_kind" == "RMAN_FULL" || "$rman_kind" == "RMAN_INCR" || "$rman_kind" == "RMAN_FULL_MANUAL" ]] \
  || die "Gecersiz tip: ${rman_kind}"

[[ -f "$CONFIG_FILE" ]] || die "Bulunamadi: $CONFIG_FILE"
[[ -d "$INSTANCES_DIR" ]] || die "Instance dizini yok: $INSTANCES_DIR"

# shellcheck source=/dev/null
source "$CONFIG_FILE"

sendftpfile() {
  local server="$1" user="$2" pass="$3" ftplog="$4" src="$5" target="$6" remote_dir="${7:-/}"
  local ftp_cd=""

  remote_dir="${remote_dir:-/}"
  remote_dir="${remote_dir// /}"
  if [[ -z "$remote_dir" ]]; then
    remote_dir="/"
  elif [[ "$remote_dir" != /* ]]; then
    remote_dir="/${remote_dir}"
  fi
  if [[ "$remote_dir" != "/" ]]; then
    remote_dir="${remote_dir%/}"
    ftp_cd="cd ${remote_dir}"
  fi

  ftp -v -n "$server" <<END_SCRIPT >"$ftplog" 2>&1
quote USER ${user}
quote PASS ${pass}
binary
${ftp_cd}
put ${src} ${target}
quit
END_SCRIPT
  if grep -qE "226 |226-" "$ftplog" 2>/dev/null; then
    echo "1"
  else
    echo "0"
  fi
}

upload_rman_folder() {
  local run_dir="$1"
  local remote_subdir="$2"
  local ftp_ok=1
  local uploaded=0
  local total_size=0

  [[ -d "$run_dir" ]] || return 1
  local remote_base="${localftpdir:-/}"
  remote_base="${remote_base%/}"
  local remote_path="${remote_base}/rman/${remote_subdir}"

  shopt -s nullglob
  local files=( "$run_dir"/* )
  shopt -u nullglob
  for file in "${files[@]}"; do
    [[ -f "$file" ]] || continue
    local base_name
    base_name="$(basename "$file")"
    local part_stat
    part_stat=$(sendftpfile \
      "$localftpip" "$localftpuser" "$localftppass" \
      "$ftplog2" \
      "$file" "$base_name" "$remote_path")
    [[ "$part_stat" == "1" ]] && uploaded=$((uploaded + 1)) || ftp_ok=0
    local psz
    psz=$(stat -c%s "$file" 2>/dev/null || echo 0)
    total_size=$((total_size + psz))
  done
  filesize=$total_size
  upload_dosyaadi="${remote_subdir}/ (${uploaded} dosya)"
  if [[ "$ftp_ok" == "1" && "$uploaded" -gt 0 ]]; then
    localftpstat="1"
  else
    localftpstat="0"
  fi
}

notify_rman_backup() {
  local api_url="http://127.0.0.1:${core_port:-8090}/api/YedekYonetimi/YedekBildirimi"
  local resp_file="${run_dir}/.api-response"
  local disip http_code

  disip="$(curl -s --max-time 5 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  disip="${disip:-0.0.0.0}"

  http_code=$(curl -s -o "$resp_file" -w "%{http_code}" --max-time 15 \
    -X POST "$api_url" \
    -H "Content-Type: application/json" \
    -d "{
      \"GuidKey\":\"${guid_key:-}\",
      \"YedekKodu\":\"${yedek_kodu:-Hbys}\",
      \"Tarih\":\"$(date '+%Y-%m-%d %H:%M:%S')\",
      \"DisIp\":\"${disip}\",
      \"DiskAlani1\":\"-\",
      \"DiskAlani2\":\"-\",
      \"DiskAlani3\":\"-\",
      \"YedekBoyutu\":\"${filesize}\",
      \"Ftp\":\"${localftpstat:-0}\",
      \"Mail\":\"$([[ "${mail_notify:-0}" == "1" ]] && echo 1 || echo 0)\",
      \"YedekTipi\":\"${rman_kind}\",
      \"Instance\":\"${INSTANCE_ID}\",
      \"Dosya\":\"${upload_dosyaadi:-}\"
    }" 2>/dev/null || echo "000")
  log "API bildirimi [${INSTANCE_ID}]: HTTP ${http_code}"
}

cleanup_old_rman_runs() {
  local type_dir="$1"
  local keep_days="${rman_retention_days:-14}"
  [[ -d "$type_dir" ]] || return 0
  find "$type_dir" -mindepth 1 -maxdepth 1 -type d -mtime +"$keep_days" -exec rm -rf {} + 2>/dev/null || true
}

detect_archivelog_mode() {
  local mode=""
  mode="$("${ORACLE_HOME}/bin/sqlplus" -s / as sysdba <<'SQL'
set pagesize 0 feedback off heading off verify off
select log_mode from v$database;
exit;
SQL
)"
  mode="$(echo "$mode" | tr -d '\r' | sed '/^$/d' | head -1 | xargs)"
  if echo "$mode" | grep -qi '^ARCHIVELOG$'; then
    echo "ARCHIVELOG"
  else
    echo "NOARCHIVELOG"
  fi
}

run_rman_instance() {
  [[ "${rman_enabled:-0}" == "1" ]] || die "Instance ${INSTANCE_ID}: RMAN kapali"

  ORACLE_HOME="${ORACLE_HOME:-}"
  if [[ -z "$ORACLE_HOME" && -d /u01/app/oracle/product ]]; then
    ORACLE_HOME="$(ls -d /u01/app/oracle/product/*/db 2>/dev/null || ls -d /u01/app/oracle/product/*/db_1 2>/dev/null | head -1)"
  fi
  [[ -x "${ORACLE_HOME}/bin/rman" ]] || die "rman bulunamadi: ${ORACLE_HOME}/bin/rman"

  export ORACLE_SID
  local tarih kurum_slug yedek_label run_subdir run_tag incr_sql arch_sql run_name run_dir log_file ftplog2
  tarih=$(date +%Y%m%d%H)
  kurum_slug="${backup_prefix:-${label:-${hastane:-${INSTANCE_ID}}}}"

  case "$rman_kind" in
    RMAN_FULL)
      yedek_label="RMANFULL"
      run_subdir="full"
      run_tag="TRTEK_HAFTALIK"
      ;;
    RMAN_INCR)
      yedek_label="RMANFARK"
      run_subdir="fark"
      run_tag="TRTEK_FARK"
      ;;
    RMAN_FULL_MANUAL)
      yedek_label="RMANMANUELFULL"
      run_subdir="full/manuel"
      run_tag="TRTEK_MANUEL"
      ;;
  esac

  run_name="${kurum_slug}${yedek_label}${tarih}"
  run_dir="${rman_dest}/${run_subdir}/${run_name}"
  mkdir -p "$run_dir"

  case "$rman_kind" in
    RMAN_FULL)
      incr_sql="BACKUP INCREMENTAL LEVEL 0 DATABASE TAG '${run_tag}' FORMAT '${run_dir}/data_%U';"
      ;;
    RMAN_INCR)
      incr_sql="BACKUP INCREMENTAL LEVEL 1 FOR RECOVER OF DATABASE WITH TAG 'TRTEK_HAFTALIK' DATABASE TAG '${run_tag}' FORMAT '${run_dir}/data_%U';"
      ;;
    RMAN_FULL_MANUAL)
      incr_sql="BACKUP INCREMENTAL LEVEL 0 DATABASE TAG '${run_tag}' FORMAT '${run_dir}/data_%U';"
      ;;
  esac

  local log_mode cold_prefix cold_suffix
  log_mode="$(detect_archivelog_mode)"
  cold_prefix=""
  cold_suffix=""

  if [[ "$log_mode" != "ARCHIVELOG" ]]; then
    if [[ "$rman_kind" == "RMAN_INCR" ]]; then
      die "Instance ${INSTANCE_ID}: Gunluk fark yedegi icin ARCHIVELOG modu gerekli (su an: ${log_mode})"
    fi
    log "NOARCHIVELOG modu [${INSTANCE_ID}]: veritabani kisa sure kapatilip MOUNT ile cold backup alinacak"
    COLD_MARKER="/yedek/orayedek/.rman-cold-${INSTANCE_ID}.flag"
    printf 'instance=%s\nsid=%s\ntip=%s\nstarted=%s\n' \
      "${INSTANCE_ID}" "${ORACLE_SID}" "${rman_kind}" "$(date -Iseconds)" >"$COLD_MARKER"
    COLD_BACKUP_ACTIVE=1
    cold_prefix=$'SHUTDOWN IMMEDIATE;\nSTARTUP MOUNT;\n'
    cold_suffix=$'\nALTER DATABASE OPEN;'
  fi

  arch_sql=""
  if [[ "${rman_archivelog_backup:-0}" == "1" ]]; then
    arch_sql="BACKUP ARCHIVELOG ALL DELETE INPUT;"
  fi

  local compress_clause=""
  if [[ "${rman_compression:-1}" == "1" ]]; then
    compress_clause="CONFIGURE DEVICE TYPE DISK PARALLELISM ${rman_channels:-2} BACKUP TYPE TO COMPRESSED BACKUPSET;"
  else
    compress_clause="CONFIGURE DEVICE TYPE DISK PARALLELISM ${rman_channels:-2};"
  fi

  local rman_cmd="${run_dir}/run.rman"
  cat >"$rman_cmd" <<RMANEOF
$compress_clause
$cold_prefix
RUN {
$(for ((i=1; i<=${rman_channels:-2}; i++)); do echo "  ALLOCATE CHANNEL ch${i} DEVICE TYPE DISK;"; done)
  $incr_sql
  BACKUP CURRENT CONTROLFILE FORMAT '${run_dir}/control_%U';
  BACKUP SPFILE FORMAT '${run_dir}/spfile_%U';
  $arch_sql
  DELETE NOPROMPT OBSOLETE RECOVERY WINDOW OF ${rman_retention_days:-14} DAYS;
$(for ((i=1; i<=${rman_channels:-2}; i++)); do echo "  RELEASE CHANNEL ch${i};"; done)
}
$cold_suffix
RMANEOF

  log_file="${run_dir}/${run_name}.log"
  ftplog2="${run_dir}/ftp.log"
  localftpstat="0"
  filesize="-1"
  upload_dosyaadi=""

  log "RMAN basliyor [${INSTANCE_ID}]: sid=${ORACLE_SID} tip=${rman_kind} mod=${log_mode} hedef=${run_dir} tag=${run_tag}"

  if ! "${ORACLE_HOME}/bin/rman" target / log "$log_file" append cmdfile="$rman_cmd" >>"$log_file" 2>&1; then
    die "RMAN hatasi — log: ${log_file}"
  fi
  if grep -qi 'ORA-19602' "$log_file"; then
    die "RMAN veri dosyasi yedegi basarisiz (NOARCHIVELOG/online) — log: ${log_file}"
  fi
  if ! grep -qi 'datafile backup set' "$log_file" || ! grep -qi 'backup set complete' "$log_file"; then
    die "RMAN veritabani yedegi tamamlanmadi — log: ${log_file}"
  fi

  if [[ "${COLD_BACKUP_ACTIVE}" == "1" ]]; then
    ensure_database_open
    rm -f "${COLD_MARKER:-}"
    COLD_BACKUP_ACTIVE=0
  fi

  shopt -s nullglob
  local pieces=( "$run_dir"/* )
  shopt -u nullglob
  local piece_count=0
  for p in "${pieces[@]}"; do
    [[ -f "$p" ]] || continue
    [[ "$(basename "$p")" == "$(basename "$log_file")" ]] && continue
    [[ "$(basename "$p")" == "run.rman" ]] && continue
    [[ "$(basename "$p")" == "ftp.log" ]] && continue
    piece_count=$((piece_count + 1))
  done
  [[ "$piece_count" -gt 0 ]] || die "RMAN parca dosyasi olusmadi: ${run_dir}"

  {
    echo "=== Sistem bilgisi $(date) instance=${INSTANCE_ID} sid=${ORACLE_SID} rman=${rman_kind} ==="
    df -h "$rman_dest" 2>/dev/null || df -h
    free -m 2>/dev/null || free
  } >>"$log_file" 2>&1

  if [[ "${ftp_upload_enabled:-0}" == "1" ]]; then
    log "FTP yukleniyor [${INSTANCE_ID}]: ${localftpip}:${localftpdir:-/}/rman/${run_subdir}/${run_name}"
    upload_rman_folder "$run_dir" "${run_subdir}/${run_name}"
  else
    localftpstat=""
    filesize=$(du -sb "$run_dir" 2>/dev/null | awk '{print $1}' || echo "-1")
    upload_dosyaadi="rman/${run_subdir}/${run_name}"
    log "FTP atlandi (pasif) [${INSTANCE_ID}]: rman/${run_subdir}/${run_name}"
  fi

  notify_rman_backup

  cleanup_old_rman_runs "${rman_dest}/${run_subdir}"
  if [[ "$rman_kind" == "RMAN_FULL_MANUAL" ]]; then
    cleanup_old_rman_runs "${rman_dest}/full/manuel"
  fi

  log "RMAN tamamlandi [${INSTANCE_ID}]: ${run_name} (${piece_count} parca) FTP=${localftpstat}"
}

run_instance_file() {
  local instance_file="$1"
  # shellcheck source=/dev/null
  source "$instance_file"
  run_rman_instance
}

if [[ -n "$only_instance" ]]; then
  instance_file="${INSTANCES_DIR}/${only_instance}.sh"
  [[ -f "$instance_file" ]] || die "Instance bulunamadi: ${only_instance}"
  run_instance_file "$instance_file"
  exit 0
fi

[[ -f "$INSTANCES_LIST" ]] || die "instances.list bulunamadi"
mapfile -t INSTANCE_IDS < "$INSTANCES_LIST"
[[ ${#INSTANCE_IDS[@]} -gt 0 ]] || die "Aktif instance yok"

for instance_id in "${INSTANCE_IDS[@]}"; do
  [[ -n "$instance_id" ]] || continue
  instance_file="${INSTANCES_DIR}/${instance_id}.sh"
  [[ -f "$instance_file" ]] || die "Instance scripti yok: ${instance_id}"
  run_instance_file "$instance_file"
done

exit 0
