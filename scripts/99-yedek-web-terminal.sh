# Yedek panel web terminali — login ortam ayarlari
[ -n "${WEB_TERMINAL:-}" ] || return 0

if [ -f /yedek/config/yedek-web-terminal-profile.sh ]; then
  # shellcheck source=/dev/null
  . /yedek/config/yedek-web-terminal-profile.sh
fi
