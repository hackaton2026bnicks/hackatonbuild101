#!/usr/bin/env bash
# Runs on the EC2 instance, invoked by the deploy workflow through SSM Run Command.
# Usage: remote-deploy.sh <ecr-registry> <image-tag> <aws-region>
set -euo pipefail

REGISTRY="$1"
IMAGE_TAG="$2"
REGION="$3"
PROJECT="build101"

cd "$(dirname "$0")"

# On the first deploy, user-data may still be installing Docker
cloud-init status --wait >/dev/null || true

param() {
  aws ssm get-parameter --region "$REGION" --name "/$PROJECT/$1" --with-decryption \
    --query Parameter.Value --output text 2>/dev/null || true
}

# Secrets live in SSM Parameter Store; .env is rebuilt on every deploy and never committed.
umask 077
cat > .env <<EOF
REGISTRY=$REGISTRY
IMAGE_TAG=$IMAGE_TAG
APP_HOST=$(param APP_HOST)
POSTGRES_PASSWORD=$(param POSTGRES_PASSWORD)
ADMIN_TOKEN=$(param ADMIN_TOKEN)
ANTHROPIC_API_KEY=$(param ANTHROPIC_API_KEY)
EOF

aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

docker compose pull
docker compose up -d --remove-orphans
docker image prune -f

docker compose ps
