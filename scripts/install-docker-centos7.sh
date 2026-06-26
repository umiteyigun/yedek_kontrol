#!/bin/bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "root olarak calistirin."
  exit 1
fi

if command -v docker >/dev/null 2>&1; then
  echo "Docker zaten kurulu: $(docker --version)"
  exit 0
fi

echo "Docker CE kuruluyor (CentOS 7)..."
yum install -y yum-utils device-mapper-persistent-data lvm2
yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker
systemctl start docker

docker --version
docker compose version

echo "Kurulum tamam. Sonraki adim:"
echo "  cd /root/yedek-docker && cp .env.example .env && nano .env && docker compose up -d"
