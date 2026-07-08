[Unit]
Description=Yedek image release updater poll timer

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
Unit=yedek-release-update.service
Persistent=true

[Install]
WantedBy=timers.target
