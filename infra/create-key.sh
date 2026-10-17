#!/usr/bin/env bash
# Create an API key for a teammate on the deployed server. The key is shown once; share it privately.
# Usage: ./infra/create-key.sh <user> [team]
set -euo pipefail
export MSYS_NO_PATHCONV=1
export AWS_PAGER=""
export AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}"

USER_NAME="${1:?Usage: $0 <user> [team]}"
TEAM="${2:-default}"
if [[ ! "$USER_NAME" =~ ^[A-Za-z0-9_.-]{1,64}$ || ! "$TEAM" =~ ^[A-Za-z0-9_.-]{1,64}$ ]]; then
  echo "User and team may only contain letters, digits, '.', '_' and '-'." >&2
  exit 1
fi

param() {
  aws ssm get-parameter --name "/build101/$1" --with-decryption --query Parameter.Value --output text | tr -d '\r'
}

APP_HOST=$(param APP_HOST)
ADMIN_TOKEN=$(param ADMIN_TOKEN)

curl -fsS -X POST "https://$APP_HOST/api/admin/keys" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"user\": \"$USER_NAME\", \"team\": \"$TEAM\"}"
echo
