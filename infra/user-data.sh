#!/bin/bash
# First-boot setup for the Amazon Linux 2023 instance. The SSM agent is preinstalled on this AMI.
set -euxo pipefail

dnf install -y docker
systemctl enable --now docker

# Docker Compose v2 plugin (not packaged for Amazon Linux 2023)
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# 2 GB swap so Postgres and the Python services fit in 2 GB of RAM
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

mkdir -p /opt/build101
