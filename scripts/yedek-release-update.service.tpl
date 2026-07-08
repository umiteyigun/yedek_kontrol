[Unit]
Description=Yedek image release updater (oneshot)
After=network-online.target docker.service yedek-docker.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=YEDEK_ROOT=__YEDEK_ROOT__
Environment=HOME=/root
EnvironmentFile=-/yedek/config/release-update.env
ExecStart=__YEDEK_ROOT__/scripts/release-updater.sh
StandardOutput=journal
StandardError=journal
