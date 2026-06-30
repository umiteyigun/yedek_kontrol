[Unit]
Description=Yedek Kontrol git auto-update poll timer

[Timer]
OnBootSec=3min
OnUnitActiveSec=2min
Unit=yedek-auto-update.service
Persistent=true

[Install]
WantedBy=timers.target
