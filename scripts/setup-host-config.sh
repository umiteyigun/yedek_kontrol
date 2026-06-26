#!/bin/bash
# Geriye uyumluluk - asil kurulum setup.sh uzerinden yapilir
exec "$(cd "$(dirname "$0")/.." && pwd)/setup.sh" "$@"
