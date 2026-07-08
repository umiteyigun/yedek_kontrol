#!/bin/bash
# Master panel kullanicisi ve session secret uretir (.env)
# Credential dosyasi git agacinda DEGIL: /yedek/credentials/master.txt
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/.env"
# Host-side secret store (public clone /opt/yedek_kontrol icinde tutulmaz)
CREDS_DIR="${YEDEK_CREDS_DIR:-/yedek/credentials}"
CREDS_FILE="${CREDS_DIR}/master.txt"
LEGACY_CREDS_FILE="${ROOT}/credentials/master.txt"

gen_pass() {
  openssl rand -base64 48 | tr -d '/+=' | head -c 32
}

gen_hex() {
  openssl rand -hex 32
}

set_env_var() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >>"$ENV_FILE"
  fi
}

write_creds_file() {
  local user="$1" pass="$2"
  cat >"$CREDS_FILE" <<EOF
================================================================================
  YEDEK PANEL - MASTER KULLANICI (guvenli yerde saklayin)
================================================================================
  Olusturma : $(date -Iseconds)
  Kullanici : ${user}
  Sifre     : ${pass}

  Not: LDAP aktif olsa bile bu kullanici ile giris yapilabilir.
  Konum    : ${CREDS_FILE}  (git/repo icinde DEGIL)
  Dosya izni: chmod 600
================================================================================
EOF
  chmod 600 "$CREDS_FILE"
}

migrate_legacy_creds() {
  # Eski public clone icindeki credentials/master.txt -> /yedek/credentials
  if [[ -f "$LEGACY_CREDS_FILE" && ! -f "$CREDS_FILE" ]]; then
    mkdir -p "$CREDS_DIR"
    chmod 700 "$CREDS_DIR"
    mv -f "$LEGACY_CREDS_FILE" "$CREDS_FILE"
    chmod 600 "$CREDS_FILE"
    echo "Eski credential tasiandi: $LEGACY_CREDS_FILE -> $CREDS_FILE"
  fi
  # Repo agacindaki credentials klasorunu temizle (sifre sizintisi olmasin)
  if [[ -d "${ROOT}/credentials" ]]; then
    rm -f "${ROOT}/credentials/master.txt" 2>/dev/null || true
    rmdir "${ROOT}/credentials" 2>/dev/null || true
  fi
}

mkdir -p "$CREDS_DIR"
chmod 700 "$CREDS_DIR"
migrate_legacy_creds
[[ -f "$ENV_FILE" ]] || cp "${ROOT}/.env.example" "$ENV_FILE"

MASTER_USER="$(grep -m1 '^MASTER_USER=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"
MASTER_USER="${MASTER_USER:-trtek-master}"

if grep -q '^MASTER_PASS=.\+' "$ENV_FILE" 2>/dev/null; then
  MASTER_PASS="$(grep -m1 '^MASTER_PASS=' "$ENV_FILE" | cut -d= -f2-)"
  if ! grep -q '^PANEL_SECRET=.\+' "$ENV_FILE" 2>/dev/null; then
    set_env_var "PANEL_SECRET" "$(gen_hex)"
  fi
  set_env_var "PANEL_USER" "$MASTER_USER"
  set_env_var "PANEL_PASS" "$MASTER_PASS"
  if [[ -f "$CREDS_FILE" ]]; then
    echo "Master kullanici zaten tanimli: ${CREDS_FILE}"
    exit 0
  fi
  write_creds_file "$MASTER_USER" "$MASTER_PASS"
  echo "Master kullanici kaydedildi: ${CREDS_FILE}"
  exit 0
fi

MASTER_PASS="$(gen_pass)"
PANEL_SECRET="$(gen_hex)"

set_env_var "MASTER_USER" "$MASTER_USER"
set_env_var "MASTER_PASS" "$MASTER_PASS"
set_env_var "PANEL_SECRET" "$PANEL_SECRET"
set_env_var "PANEL_USER" "$MASTER_USER"
set_env_var "PANEL_PASS" "$MASTER_PASS"

write_creds_file "$MASTER_USER" "$MASTER_PASS"
echo "Master kullanici olusturuldu: ${CREDS_FILE}"
