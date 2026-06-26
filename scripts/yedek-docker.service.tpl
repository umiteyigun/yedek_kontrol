[Unit]
Description=TRTEK Yedek Docker Stack (yedek-core panel + API + FTP)
Documentation=file://__YEDEK_ROOT__/setup.sh
Requires=docker.service
After=docker.service network.target
Wants=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=__YEDEK_ROOT__
Environment=YEDEK_DOCKER_ROOT=__YEDEK_ROOT__
ExecStart=__YEDEK_ROOT__/scripts/yedek-docker-ctl.sh start
ExecStop=__YEDEK_ROOT__/scripts/yedek-docker-ctl.sh stop
ExecReload=__YEDEK_ROOT__/scripts/yedek-docker-ctl.sh restart
TimeoutStartSec=300
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
