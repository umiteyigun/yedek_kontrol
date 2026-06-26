#!/bin/bash
# Yedek panel web terminal — tum kullanicilar icin temiz oturum
if [ -z "${WEB_TERMINAL:-}" ]; then
  return 0 2>/dev/null || exit 0
fi

unset MAILCHECK

# Alt kabuklarda (su -) job control uyarisi
if [[ $- == *i* ]]; then
  set +o monitor 2>/dev/null || true
fi

# Guard: login profili bittikten sonra yukle (extdebug -> bashdb uyarisi onlenir)
_yedek_arm_terminal_guard() {
  [ "${_YEDEK_GUARD_READY:-}" = "1" ] && return 0
  [ -z "${PS1:-}" ] && return 0
  if [ -f /yedek/config/yedek-web-terminal-guard.sh ]; then
    # shellcheck source=/dev/null
    . /yedek/config/yedek-web-terminal-guard.sh
  fi
  _YEDEK_GUARD_READY=1
}

case "${PROMPT_COMMAND:-}" in
  *_yedek_arm_terminal_guard*) ;;
  *) PROMPT_COMMAND="_yedek_arm_terminal_guard${PROMPT_COMMAND:+; }${PROMPT_COMMAND:-}" ;;
esac
