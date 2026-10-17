#!/usr/bin/env bash
# Create an API key for a teammate on the deployed server. The key is shown once; share it privately.
# Usage: ./infra/create-key.sh <user> [team]
exec "$(dirname "$0")/keys.sh" create "$@"
