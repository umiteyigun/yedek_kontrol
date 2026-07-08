#!/bin/bash
# setup.sh icin interaktif sorular ve env yazma yardimcilari.
# Kaynak: setup.sh (non-interactive modda atlanir)
set -euo pipefail

PROMPT_ROOT="${PROMPT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

SETUP_SKIPPED_AGENT=0
SETUP_SKIPPED_AUTOUPDATE=0
SETUP_CUSTOM_MASTER=0

is_interactive() {
  [[ -t 0 && -t 1 && "${SETUP_NONINTERACTIVE:-0}" != 1 ]]
}

section() {
  echo
  echo "────────────────────────────────────────"
  echo "  $*"
  echo "────────────────────────────────────────"
}

prompt_line() {
  local label="$1" default="${2:-}" val=""
  if [[ -n "$default" ]]; then
    read -r -p "${label} [${default}]: " val
    val="${val:-$default}"
  else
    read -r -p "${label}: " val
  fi
  printf '%s' "$val"
}

prompt_secret() {
  local label="$1" val=""
  read -r -s -p "${label} (bos=atla): " val
  echo
  printf '%s' "$val"
}

prompt_yn() {
  local label="$1" default="${2:-e}" ans=""
  read -r -p "${label} [${default}]: " ans
  ans="${ans:-$default}"
  case "$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')" in
    e|evet|y|yes) return 0 ;;
    *) return 1 ;;
  esac
}

set_kv() {
  local file="$1" key="$2" val="$3"
  touch "$file"
  chmod 600 "$file"
  if grep -q "^${key}=" "$file" 2>/dev/null; then
    local tmp
    tmp="$(mktemp)"
    while IFS= read -r line || [[ -n "$line" ]]; do
      if [[ "$line" == "${key}="* ]]; then
        printf '%s=%s\n' "$key" "$val"
      else
        printf '%s\n' "$line"
      fi
    done <"$file" >"$tmp"
    mv "$tmp" "$file"
    chmod 600 "$file"
  else
    printf '%s=%s\n' "$key" "$val" >>"$file"
  fi
}

get_kv() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 1
  grep -m1 "^${key}=" "$file" 2>/dev/null | cut -d= -f2- || true
}

write_central_agent_env() {
  local dst="/yedek/config/central-agent.env"
  local code="$1" hub_http="$2" hub_ws="$3" reg_secret="$4" proxy_secret="$5" label="$6" role="$7"
  mkdir -p /yedek/config/agent-state
  cat >"$dst" <<EOF
# Merkez hub baglantisi — setup.sh ile olusturuldu
ORG_ENROLLMENT_CODE=${code}
HUB_HTTP_URL=${hub_http}
HUB_WS_URL=${hub_ws}
PANEL_LOCAL_URL=https://127.0.0.1:${PANEL_HTTPS_PORT:-8443}
NODE_LABEL=${label}
NODE_ROLE=${role}
AGENT_VERIFY_TLS=0
AGENT_STATE_DIR=/var/lib/yedek-agent
HUB_AGENT_REGISTER_SECRET=${reg_secret}
CENTRAL_PROXY_SECRET=${proxy_secret}
EOF
  chmod 600 "$dst"
}

hub_ws_from_http() {
  local http="$1" ws
  ws="${http/http/ws}/agent/v1"
  ws="${ws/https/wss}"
  printf '%s' "$ws"
}

interactive_panel_config() {
  local env_file="$PROMPT_ROOT/.env"
  [[ -f "$env_file" ]] || cp "$PROMPT_ROOT/.env.example" "$env_file"

  if grep -q '^MASTER_PASS=.\+' "$env_file" 2>/dev/null; then
    echo "Master kullanici zaten tanimli ($(get_kv "$env_file" MASTER_USER))."
    if ! prompt_yn "Master bilgilerini degistirmek ister misiniz?" "h"; then
      return 0
    fi
  fi

  section "Panel / Master kullanici"
  echo "Master kullanici LDAP kapaliyken ve acil erisim icin kullanilir."
  echo "Bos birakirsaniz sifre otomatik uretilir."

  local user pass tz
  user="$(prompt_line "Master kullanici adi" "$(get_kv "$env_file" MASTER_USER || echo trtek-master)")"
  pass="$(prompt_secret "Master sifre")"
  tz="$(prompt_line "Saat dilimi (TZ)" "$(get_kv "$env_file" TZ || echo Europe/Istanbul)")"

  set_kv "$env_file" MASTER_USER "$user"
  set_kv "$env_file" PANEL_USER "$user"
  set_kv "$env_file" TZ "$tz"
  if [[ -n "$pass" ]]; then
    set_kv "$env_file" MASTER_PASS "$pass"
    set_kv "$env_file" PANEL_PASS "$pass"
    SETUP_CUSTOM_MASTER=1
  fi

  export MASTER_USER="$user"
  export MASTER_PASS="${pass:-}"
  export TZ="$tz"
}

interactive_autoupdate_config() {
  local dst="/yedek/config/auto-update.env"
  local example="$PROMPT_ROOT/config/auto-update.example.env"

  [[ -d "$PROMPT_ROOT/.git" ]] || {
    SETUP_SKIPPED_AUTOUPDATE=1
    return 0
  }

  if [[ -f "$dst" ]] && grep -q '^YEDEK_ROOT=' "$dst" 2>/dev/null; then
    echo "Oto-guncelleme config mevcut: $dst"
    if ! prompt_yn "Oto-guncelleme ayarlarini yeniden girmek ister misiniz?" "h"; then
      return 0
    fi
  fi

  section "Git oto-guncelleme (git.trtek.tr)"
  echo "Repo: $(get_kv "$example" AUTO_UPDATE_REPO_URL || echo https://git.trtek.tr/umiteyigun/yedek_kontrol.git)"
  echo "Her ~2 dakikada yeni commit kontrol edilir."

  if ! prompt_yn "Oto-guncelleme aktif olsun mu?" "e"; then
    [[ -f "$example" ]] && install -m 600 "$example" "$dst"
    set_kv "$dst" AUTO_UPDATE_ENABLED "0"
    set_kv "$dst" YEDEK_ROOT "$PROMPT_ROOT"
    SETUP_SKIPPED_AUTOUPDATE=1
    return 0
  fi

  [[ -f "$dst" ]] || install -m 600 "$example" "$dst"
  set_kv "$dst" AUTO_UPDATE_ENABLED "1"
  set_kv "$dst" YEDEK_ROOT "$PROMPT_ROOT"
  set_kv "$dst" AUTO_UPDATE_BRANCH "$(get_kv "$dst" AUTO_UPDATE_BRANCH || echo main)"
  set_kv "$dst" AUTO_UPDATE_REPO_URL "$(get_kv "$example" AUTO_UPDATE_REPO_URL || echo https://git.trtek.tr/umiteyigun/yedek_kontrol.git)"

  echo
  echo "git.trtek.tr private repo icin Gitea token girin (readonly veya full)."
  local token
  token="$(prompt_secret "Git token (AUTO_UPDATE_GIT_TOKEN)")"
  if [[ -n "$token" ]]; then
    set_kv "$dst" AUTO_UPDATE_GIT_TOKEN "$token"
  fi
}

interactive_agent_config() {
  local dst="/yedek/config/central-agent.env"

  if [[ -f "$dst" ]] && grep -q '^ORG_ENROLLMENT_CODE=.\+' "$dst" 2>/dev/null; then
    echo "Merkez agent config mevcut: $dst"
    if ! prompt_yn "Agent ayarlarini yeniden girmek ister misiniz?" "h"; then
      return 0
    fi
  fi

  section "Merkez hub agent"
  cat <<'HELP'

  Bu sunucuyu merkez panele (hub) baglar. Degerlerin TAMAMI merkez yoneticisinden
  alinir — hub sunucusundaki kurum kaydi ve config dosyasindan.

  Alan              Ne ise yarar?                          Ornek / nereden?
  ----------------  -------------------------------------  --------------------------
  ORG_ENROLLMENT_   Kuruma ozel kayit kodu. Hub'da         SAKARYA-ADS-2024
  CODE              kurum olusturulunca verilir.           USKUDAR-DB-PRIMARY
                                                           (hub -> Kurumlar ekrani)

  HUB_HTTP_URL      Merkez hub HTTPS adresi (agent         https://10.0.0.5:8444
                    kayit API buraya gider).               https://yedek-hub.sirket.local:8444

  HUB_AGENT_        Hub ile paylasilan gizli anahtar.      hub: HUB_AGENT_REGISTER_SECRET
  REGISTER_SECRET   Ilk agent kaydinda zorunlu.            (aynı degeri buraya yapistir)
                    Merkez /root/yedek_central/config/hub*.env

  CENTRAL_PROXY_    Merkezden bu panele SSO/proxy icin     hub: HUB_CENTRAL_PROXY_SECRET
  SECRET            imza anahtari (min 32 karakter).       (aynı degeri buraya yapistir)
                    Client ve hub BIREBIR ayni olmali.

  NODE_LABEL        Bu sunucunun hub'daki kisa adi.       primary, standby, dg2
                    Ayni kurumda birden fazla sunucu
                    varsa birbirinden ayirt eder.

  NODE_ROLE         Sunucu rolu (hub listesinde gorunur).  PRIMARY veya STANDBY
                    Oracle DG: biri PRIMARY digeri STANDBY.

HELP

  if ! prompt_yn "Simdi merkez agent ayarlarini girmek istiyor musunuz?" "h"; then
    SETUP_SKIPPED_AGENT=1
    local dst="/yedek/config/central-agent.env"
    [[ -f "$dst" ]] || install -m 600 "$PROMPT_ROOT/config/central-agent.example.env" "$dst"
    return 0
  fi

  local code hub_http hub_ws reg_secret proxy_secret label role
  code="$(prompt_line "Kurum kayit kodu (ORG_ENROLLMENT_CODE)" "")"
  while [[ -z "$code" ]]; do
    echo "  Zorunlu. Hub'da kurum olusturulunca verilen kod (ornek: SAKARYA-ADS-2024)."
    code="$(prompt_line "Kurum kayit kodu" "")"
  done
  code="$(printf '%s' "$code" | tr '[:lower:]' '[:upper:]')"

  echo "  Ornek: https://10.0.0.5:8444 — merkez sunucunun IP/hostname + 8444 portu"
  hub_http="$(prompt_line "Hub HTTPS adresi (HUB_HTTP_URL)" "https://MERKEZ_IP:8444")"
  hub_ws="$(hub_ws_from_http "$hub_http")"
  echo "  WebSocket otomatik: $hub_ws"

  echo
  echo "  Asagidaki iki anahtar merkez yoneticisinden kopyalanir."
  echo "  Hub dosyasi: /root/yedek_central/config/hub.dev.env (veya prod .env)"
  reg_secret="$(prompt_secret "Hub kayit anahtari (HUB_AGENT_REGISTER_SECRET = hub HUB_AGENT_REGISTER_SECRET)")"
  proxy_secret="$(prompt_secret "Proxy imza anahtari (CENTRAL_PROXY_SECRET = hub HUB_CENTRAL_PROXY_SECRET, min 32 karakter)")"

  label="$(prompt_line "Sunucu etiketi (NODE_LABEL) — hub'da gorunen ad" "primary")"
  echo "  Ornek: primary (ana), standby (yedek), dg2 (ikinci DataGuard node)"
  role="$(prompt_line "Sunucu rolu (NODE_ROLE)" "PRIMARY")"
  echo "  Ornek: PRIMARY veya STANDBY"

  write_central_agent_env "$code" "$hub_http" "$hub_ws" "$reg_secret" "$proxy_secret" "$label" "$role"

  if [[ -z "$reg_secret" || -z "$proxy_secret" ]]; then
    echo
    echo "  UYARI: Kayit veya proxy secret bos birakildi."
    echo "  Agent baslar ama hub kaydi / merkez SSO calismayabilir."
    echo "  Sonra duzenlemek icin: /yedek/config/central-agent.env"
  fi

  export ORG_ENROLLMENT_CODE="$code"
  export HUB_HTTP_URL="$hub_http"
  export HUB_WS_URL="$hub_ws"
}

interactive_configure() {
  if ! is_interactive; then
    echo "[setup] Non-interactive mod (SETUP_NONINTERACTIVE=1 veya TTY yok)"
    echo "[setup] Varsayilan config kullanilacak; kurulum sonunda dosya yollari listelenecek."
    return 0
  fi

  echo
  echo "================================================================================"
  echo "  KURULUM SORULARI"
  echo "  Bos birakilan opsiyonel alanlar sonra config dosyalarindan duzenlenebilir."
  echo "================================================================================"

  interactive_panel_config
  interactive_autoupdate_config
  interactive_agent_config
}

print_config_hints() {
  local agent_cfg="/yedek/config/central-agent.env"
  local auto_cfg="/yedek/config/auto-update.env"
  local env_file="$PROMPT_ROOT/.env"
  local pending=0

  echo
  echo "  Yapilandirma dosyalari:"
  echo "    Panel / master    : $env_file"
  echo "    Master sifre      : /yedek/credentials/master.txt"
  echo "    Oracle ayarlari   : $PROMPT_ROOT/config/settings.json (panelden de)"
  echo "    Oto-guncelleme    : $auto_cfg"
  echo "    Merkez agent      : $agent_cfg"

  if [[ -f "$agent_cfg" ]]; then
    # shellcheck source=/dev/null
    source "$agent_cfg"
    if [[ -z "${ORG_ENROLLMENT_CODE:-}" ]]; then
      pending=1
      echo
      echo "  >> Merkez agent henuz yapilandirilmadi: $agent_cfg"
      echo "     ORG_ENROLLMENT_CODE  — hub kurum kodu (ornek: SAKARYA-ADS-2024)"
      echo "     HUB_HTTP_URL         — merkez adres (ornek: https://10.0.0.5:8444)"
      echo "     HUB_AGENT_REGISTER_SECRET — hub HUB_AGENT_REGISTER_SECRET ile ayni"
      echo "     CENTRAL_PROXY_SECRET — hub HUB_CENTRAL_PROXY_SECRET ile ayni"
      echo "     Sonra: cd $PROMPT_ROOT && docker compose --profile central up -d --build central-agent"
    elif [[ -z "${HUB_AGENT_REGISTER_SECRET:-}" || -z "${CENTRAL_PROXY_SECRET:-}" ]]; then
      pending=1
      echo
      echo "  >> Merkez agent eksik anahtarlar:"
      echo "     Dosya: $agent_cfg"
      [[ -z "${HUB_AGENT_REGISTER_SECRET:-}" ]] && echo "     - HUB_AGENT_REGISTER_SECRET (hub ile ayni)"
      [[ -z "${CENTRAL_PROXY_SECRET:-}" ]] && echo "     - CENTRAL_PROXY_SECRET (hub ile ayni)"
      echo "     Duzenledikten sonra: docker compose --profile central up -d --build central-agent"
    fi
  else
    pending=1
    echo
    echo "  >> Merkez agent config yok. Kurulum sonrasi: bash $PROMPT_ROOT/setup.sh"
  fi

  if [[ -f "$auto_cfg" ]]; then
    # shellcheck source=/dev/null
    source "$auto_cfg"
    if [[ "${AUTO_UPDATE_ENABLED:-1}" != "1" ]]; then
      echo
      echo "  >> Oto-guncelleme kapali. Acmak icin: $auto_cfg -> AUTO_UPDATE_ENABLED=1"
      echo "     Sonra: systemctl start yedek-auto-update.timer"
    elif [[ -z "${AUTO_UPDATE_GIT_TOKEN:-}" ]]; then
      echo
      echo "  >> git.trtek.tr icin token: $auto_cfg -> AUTO_UPDATE_GIT_TOKEN"
    fi
  fi

  if [[ "$pending" -eq 0 ]]; then
    echo
    echo "  Tum zorunlu yapilandirmalar tamam gorunuyor."
  fi
}
