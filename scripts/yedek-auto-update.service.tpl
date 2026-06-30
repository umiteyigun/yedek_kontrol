[Unit]
Description=Yedek Kontrol git auto-update (oneshot)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=YEDEK_ROOT=__YEDEK_ROOT__
EnvironmentFile=-/yedek/config/auto-update.env
ExecStart=__YEDEK_ROOT__/scripts/yedek-auto-update.sh
StandardOutput=journal
StandardError=journal
