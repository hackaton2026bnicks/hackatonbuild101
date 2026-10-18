#!/usr/bin/env bash
# Manage team API keys on the deployed server.
# The admin API is blocked from the internet, so the command runs inside the server through SSM.
# Usage: ./infra/keys.sh create <user> [team] | list | revoke <id> | repair-text [--apply] | repair-tasks [--apply]
set -euo pipefail
export MSYS_NO_PATHCONV=1
export AWS_PAGER=""
export AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}"

usage() {
  echo "Usage: $0 create <user> [team] | list | revoke <id> | repair-text [--apply] | repair-tasks [--apply]" >&2
  exit 1
}

NAME='^[A-Za-z0-9_.-]{1,64}$'
case "${1:-}" in
  create)
    [[ "${2:-}" =~ $NAME && "${3:-default}" =~ $NAME ]] || usage
    ARGS="create-key $2 ${3:-default}"
    ;;
  list)
    ARGS="list-keys"
    ;;
  revoke)
    [[ "${2:-}" =~ ^[0-9]+$ ]] || usage
    ARGS="revoke-key $2"
    ;;
  repair-tasks)
    # Recomputes each session's task from its first real request; without --apply it only reports
    [[ -z "${2:-}" || "${2:-}" == "--apply" ]] || usage
    ARGS="repair-tasks ${2:-}"
    ;;
  repair-text)
    # Fixes accents garbled before PR #22; without --apply it only reports what it would change
    [[ -z "${2:-}" || "${2:-}" == "--apply" ]] || usage
    ARGS="repair-text ${2:-}"
    ;;
  *)
    usage
    ;;
esac

INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=build101" "Name=instance-state-name,Values=running" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text | tr -d '\r')
if [ "$INSTANCE_ID" = "None" ]; then
  echo "The server is not running. Start it with ./infra/power.sh start" >&2
  exit 1
fi

COMMAND_ID=$(aws ssm send-command \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "build101 keys $1" \
  --parameters "commands=bash /opt/build101/infra/server-admin.sh $ARGS" \
  --query Command.CommandId --output text | tr -d '\r')

STATUS=Pending
for _ in $(seq 1 30); do
  sleep 2
  STATUS=$(aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$INSTANCE_ID" \
    --query Status --output text 2>/dev/null | tr -d '\r' || echo Pending)
  case "$STATUS" in
    Pending|InProgress|Delayed) ;;
    *) break ;;
  esac
done

aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$INSTANCE_ID" \
  --query StandardOutputContent --output text | tr -d '\r'
if [ "$STATUS" != "Success" ]; then
  aws ssm get-command-invocation --command-id "$COMMAND_ID" --instance-id "$INSTANCE_ID" \
    --query StandardErrorContent --output text | tr -d '\r' >&2
  exit 1
fi
