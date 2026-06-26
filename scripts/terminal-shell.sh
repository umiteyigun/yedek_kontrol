#!/bin/bash
# Web terminal login shell — renkli ve temiz oturum.
export WEB_TERMINAL=1
export YEDEK_TERMINAL_GUARD=1
export TERM="${TERM:-xterm-256color}"
export COLORTERM="${COLORTERM:-truecolor}"
export CLORTERM="${CLORTERM:-true}"
export CLICOLOR=1
export FORCE_COLOR=1

if [ "$TERM" = "dumb" ] || [ -z "$TERM" ]; then
  export TERM=xterm-256color
fi

# Sifre komutlarini stub'la (PATH); guard bashrc sonrasi PROMPT_COMMAND ile yuklenir
export PATH="/yedek/config/terminal-bin:${PATH}"

# CentOS abrt login bildirimi
abrt-cli() { return 0; }
export -f abrt-cli 2>/dev/null || true

touch "${HOME}/.hushlogin" 2>/dev/null || true

cd "${HOME:-/root}" 2>/dev/null || true

exec /bin/bash --login -i
