#!/bin/bash
# Yedek panel web terminal — sifre ve hassas dosya korumasi
if [ -z "${WEB_TERMINAL:-}" ] && [ -z "${YEDEK_TERMINAL_GUARD:-}" ]; then
  return 0 2>/dev/null || exit 0
fi

export YEDEK_TERMINAL_GUARD=1

# Onceki oturumdan kalma bozuk export -f fonksiyonlarini temizle
unset -f passwd chpasswd chage vipw vigr htpasswd usermod useradd userdel 2>/dev/null || true
unset -f cat head tail less more vi vim nano ed sed awk tee cp mv rm 2>/dev/null || true

# root .bashrc alias'lari (cp -i vb.) fonksiyon tanimini bozar
unalias cp mv rm 2>/dev/null || true

_YEDEK_GUARD_MSG='[Yedek Terminal] Sifre degistirme ve hassas dosya erisimi panel terminalinde kapali.'

_yedek_blocked_path() {
  local raw="$1"
  local real
  [ -z "$raw" ] && return 1
  case "$raw" in
    -*) return 1 ;;
  esac
  real=$(readlink -f "$raw" 2>/dev/null || echo "$raw")
  case "$real" in
    /etc/passwd|/etc/shadow|/etc/gshadow|/etc/security/opasswd|/etc/security/passwd)
      return 0
      ;;
  esac
  case "$real" in
    */local_users.json|*/config/local_users.json)
      return 0
      ;;
  esac
  return 1
}

_yedek_guard_paths() {
  local arg
  for arg in "$@"; do
    if _yedek_blocked_path "$arg"; then
      echo "$_YEDEK_GUARD_MSG" >&2
      echo "Engellenen yol: $arg" >&2
      return 126
    fi
  done
  return 0
}

usermod() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -p|--password|-e|--expiredate)
        echo "$_YEDEK_GUARD_MSG" >&2
        return 126
        ;;
    esac
  done
  command usermod "$@"
}

useradd() {
  local arg
  for arg in "$@"; do
    case "$arg" in
      -p|--password)
        echo "$_YEDEK_GUARD_MSG" >&2
        return 126
        ;;
    esac
  done
  command useradd "$@"
}

userdel() { echo "$_YEDEK_GUARD_MSG" >&2; return 126; }

cat() {
  _yedek_guard_paths "$@" || return $?
  command cat "$@"
}

head() {
  _yedek_guard_paths "$@" || return $?
  command head "$@"
}

tail() {
  _yedek_guard_paths "$@" || return $?
  command tail "$@"
}

less() {
  _yedek_guard_paths "$@" || return $?
  command less "$@"
}

more() {
  _yedek_guard_paths "$@" || return $?
  command more "$@"
}

vi() {
  _yedek_guard_paths "$@" || return $?
  command vi "$@"
}

vim() {
  _yedek_guard_paths "$@" || return $?
  command vim "$@"
}

nano() {
  _yedek_guard_paths "$@" || return $?
  command nano "$@"
}

ed() {
  _yedek_guard_paths "$@" || return $?
  command ed "$@"
}

sed() {
  local arg
  for arg in "$@"; do
    _yedek_blocked_path "$arg" && { echo "$_YEDEK_GUARD_MSG" >&2; return 126; }
  done
  command sed "$@"
}

awk() {
  local arg
  for arg in "$@"; do
    _yedek_blocked_path "$arg" && { echo "$_YEDEK_GUARD_MSG" >&2; return 126; }
  done
  command awk "$@"
}

tee() {
  _yedek_guard_paths "$@" || return $?
  command tee "$@"
}

cp() {
  local args=("$@")
  local dest="${args[@]: -1}"
  _yedek_blocked_path "$dest" && { echo "$_YEDEK_GUARD_MSG" >&2; return 126; }
  command cp "$@"
}

mv() {
  local args=("$@")
  local dest="${args[@]: -1}"
  _yedek_blocked_path "$dest" && { echo "$_YEDEK_GUARD_MSG" >&2; return 126; }
  command mv "$@"
}

rm() {
  _yedek_guard_paths "$@" || return $?
  command rm "$@"
}

_yedek_guard_blocked_binary() {
  local cmd="$1"
  local base
  base=$(basename "$cmd" 2>/dev/null || echo "$cmd")
  case "$base" in
    passwd|chpasswd|chage|vipw|vigr|htpasswd|userdel)
      return 0
      ;;
  esac
  return 1
}

_yedek_guard_debug() {
  local cmd="${BASH_COMMAND%% *}"
  case "$cmd" in
    _yedek_*|trap|source|.|return|export|unset|true|false)
      return 0
      ;;
  esac
  if _yedek_guard_blocked_binary "$cmd"; then
    echo "$_YEDEK_GUARD_MSG" >&2
    return 126
  fi
  case "$BASH_COMMAND" in
    *local_users.json*|*/etc/passwd*|*/etc/shadow*|*/etc/gshadow*)
      echo "$_YEDEK_GUARD_MSG" >&2
      return 126
      ;;
  esac
  return 0
}

if [[ $- == *i* ]]; then
  _yedek_install_debug_guard() {
    shopt -s extdebug 2>/dev/null || true
    if [ "${_YEDEK_DEBUG_GUARD:-}" != "1" ]; then
      trap '_yedek_guard_debug' DEBUG
      _YEDEK_DEBUG_GUARD=1
    fi
  }
  _yedek_install_debug_guard
  case "${PROMPT_COMMAND:-}" in
    *_yedek_install_debug_guard*) ;;
    *) PROMPT_COMMAND="_yedek_install_debug_guard${PROMPT_COMMAND:+; }${PROMPT_COMMAND:-}" ;;
  esac
fi
