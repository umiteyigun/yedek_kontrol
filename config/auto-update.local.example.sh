# /yedek/config/auto-update.local.sh — kurum sunucusunda (repoda degil)
# git pull sonrasi asla ezilmemesi gereken dosyalar.

AUTO_UPDATE_PRESERVE=(
  config/settings.json
  config/sessions.json
  config/generated
  credentials
  .env
)

# Deploy sonrasi calistirilacak komutlar (opsiyonel)
# AUTO_UPDATE_POST_DEPLOY=(
#   'echo "ozel islem"'
# )
