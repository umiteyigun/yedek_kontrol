#!/bin/bash
# Master panel kullanicisi ve session secret uretir (.env)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT}/.env"
CREDS_DIR="${ROOT}/credentials"
CREDS_FILE="${CREDS_DIR}/master.txt"

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

mkdir -p "$CREDS_DIR"
chmod 700 "$CREDS_DIR"
[[ -f "$ENV_FILE" ]] || cp "${ROOT}/.env.example" "$ENV_FILE"

if grep -q '^MASTER_PASS=.\+' "$ENV_FILE" 2>/dev/null; then
  echo "MASTER_PASS zaten tanimli: ${CREDS_FILE}"
  exit 0
fi

MASTER_USER="trtek-master"
MASTER_PASS="$(gen_pass)"
PANEL_SECRET="$(gen_hex)"

set_env_var "MASTER_USER" "$MASTER_USER"
set_env_var "MASTER_PASS" "$MASTER_PASS"
set_env_var "PANEL_SECRET" "$PANEL_SECRET"
set_env_var "PANEL_USER" "$MASTER_USER"
set_env_var "PANEL_PASS" "$MASTER_PASS"

cat >"$CREDS_FILE" <<EOF
================================================================================
  YEDEK PANEL - MASTER KULLANICI (guvenli yerde saklayin)
================================================================================
  Olusturma : $(date -Iseconds)
  Kullanici : ${MASTER_USER}
  Sifre     : ${MASTER_PASS}

  Not: LDAP aktif olsa bile bu kullanici ile giris yapilabilir.
  Dosya izni: chmod 600
================================================================================
EOF
chmod 600 "$CREDS_FILE"

echo "Master kullanici olusturuldu: ${CREDS_FILE}"
