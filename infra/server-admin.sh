#!/usr/bin/env bash
# Runs on the EC2 instance (through SSM, from infra/keys.sh): admin commands inside the API container.
# Usage: server-admin.sh create-key <user> [team] | list-keys | revoke-key <id>
set -euo pipefail
cd "$(dirname "$0")"
docker compose exec -T api python -m api.admin "$@"
