#!/usr/bin/env bash
# Start or stop the team server to save money. A stopped instance only costs its disk and IP.
# Usage: ./infra/power.sh start|stop|status
set -euo pipefail
export MSYS_NO_PATHCONV=1
export AWS_PAGER=""
export AWS_DEFAULT_REGION="${AWS_REGION:-us-east-1}"

PROJECT="build101"
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Project,Values=$PROJECT" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text | tr -d '\r')

if [ "$INSTANCE_ID" = "None" ]; then
  echo "No $PROJECT instance found. Run infra/bootstrap.sh first." >&2
  exit 1
fi

case "${1:-status}" in
  start)
    aws ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null
    aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
    echo "$INSTANCE_ID running. Containers restart on their own."
    ;;
  stop)
    aws ec2 stop-instances --instance-ids "$INSTANCE_ID" >/dev/null
    echo "$INSTANCE_ID stopping. Data in Postgres is kept on disk."
    ;;
  status)
    aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
      --query 'Reservations[0].Instances[0].[InstanceId,InstanceType,State.Name]' --output text
    ;;
  *)
    echo "Usage: $0 start|stop|status" >&2
    exit 1
    ;;
esac
