#!/bin/bash
# Mount tabanli disk doluluk raporu (merkezi API: DiskAlani1-3)
# DiskAlani1 = / (kok)
# DiskAlani2 = yedek dizininin mount noktasi / ile farkliysa (boot vb. sistem mount'lari haric)
# DiskAlani3 = simdilik 0 (ek ayri mount yoksa)
#
# Kullanim: disk-report.sh [yedek_dizini]
# Cikti: DiskAlani1=99% DiskAlani2=0 DiskAlani3=0
set -euo pipefail

backup_path="${1:-/yedek/orayedek}"

_resolve_path() {
  local p="${1%/}"
  while [[ -n "$p" && "$p" != "/" && ! -e "$p" ]]; do
    p="$(dirname "$p")"
  done
  echo "${p:-/}"
}

_df_fields() {
  local path="$1"
  df -P "$path" 2>/dev/null | awk 'NR==2 {print $1 "\t" $5 "\t" $NF}'
}

_is_ignored_mount() {
  local mp="${1:-}"
  case "$mp" in
    /boot|/boot/*) return 0 ;;
  esac
  return 1
}

backup_path="$(_resolve_path "$backup_path")"

root_line="$(_df_fields /)"
backup_line="$(_df_fields "$backup_path")"

root_src=$(echo "$root_line" | awk -F'\t' '{print $1}')
disk1=$(echo "$root_line" | awk -F'\t' '{print $2}')
root_mp=$(echo "$root_line" | awk -F'\t' '{print $3}')

backup_src=$(echo "$backup_line" | awk -F'\t' '{print $1}')
backup_pct=$(echo "$backup_line" | awk -F'\t' '{print $2}')
backup_mp=$(echo "$backup_line" | awk -F'\t' '{print $3}')

disk1=${disk1:-0%}
disk2="0"
disk3="0"

# Ayri partition/disk: yedek mount'u kokten farkliysa DiskAlani2 (/boot vb. sayilmaz)
if ! _is_ignored_mount "$backup_mp"; then
  if [[ -n "$backup_mp" && -n "$root_mp" && "$backup_mp" != "$root_mp" ]]; then
    disk2=${backup_pct:-0%}
  elif [[ -n "$backup_src" && -n "$root_src" && "$backup_src" != "$root_src" ]]; then
    disk2=${backup_pct:-0%}
  fi
fi

printf 'DiskAlani1=%s\nDiskAlani2=%s\nDiskAlani3=%s\n' "$disk1" "$disk2" "$disk3"
