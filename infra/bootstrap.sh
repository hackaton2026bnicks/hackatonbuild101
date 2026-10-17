#!/usr/bin/env bash
# One-time AWS setup for the team server. Safe to re-run: existing resources are reused.
#
# Creates: ECR repositories, the EC2 instance role, a security group (80/443 only),
# a t3.small instance with Docker, an Elastic IP, SSM parameters with the app secrets,
# the GitHub OIDC provider and the role the deploy workflow assumes, and an optional budget alert.
#
# Usage (from the repo root, in Git Bash or any bash):
#   ALERT_EMAIL=you@example.com ANTHROPIC_API_KEY=sk-ant-... ./infra/bootstrap.sh
#
# Optional variables: AWS_REGION (default us-east-1), INSTANCE_TYPE (default t3.small),
# BUDGET_USD (default 25), GITHUB_REPO (default hackaton2026bnicks/hackatonbuild101).
set -euo pipefail

# Git Bash would otherwise rewrite arguments like "/build101/APP_HOST" into Windows paths
export MSYS_NO_PATHCONV=1
export AWS_PAGER=""

PROJECT="build101"
REGION="${AWS_REGION:-us-east-1}"
GITHUB_REPO="${GITHUB_REPO:-hackaton2026bnicks/hackatonbuild101}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.small}"
BUDGET_USD="${BUDGET_USD:-25}"
ALERT_EMAIL="${ALERT_EMAIL:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export AWS_DEFAULT_REGION="$REGION"

# The AWS CLI on Windows ends lines with \r, which breaks string comparisons
awsq() { aws "$@" | tr -d '\r'; }
step() { printf '\n==> %s\n' "$*"; }

ACCOUNT_ID=$(awsq sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
step "Account $ACCOUNT_ID, region $REGION"

step "ECR repositories"
for svc in api worker mockbank; do
  repo="$PROJECT-$svc"
  if ! aws ecr describe-repositories --repository-names "$repo" >/dev/null 2>&1; then
    aws ecr create-repository --repository-name "$repo" >/dev/null
    echo "created $repo"
  else
    echo "exists  $repo"
  fi
  # Keep only the last 10 images so storage stays near zero
  aws ecr put-lifecycle-policy --repository-name "$repo" --lifecycle-policy-text \
    '{"rules":[{"rulePriority":1,"description":"keep last 10 images","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":10},"action":{"type":"expire"}}]}' \
    >/dev/null
done

ensure_role() {
  local name="$1" trust="$2"
  if aws iam get-role --role-name "$name" >/dev/null 2>&1; then
    aws iam update-assume-role-policy --role-name "$name" --policy-document "$trust"
    echo "exists  role $name"
  else
    aws iam create-role --role-name "$name" --assume-role-policy-document "$trust" >/dev/null
    echo "created role $name"
  fi
}

step "EC2 instance role"
EC2_ROLE="$PROJECT-ec2"
ensure_role "$EC2_ROLE" '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name "$EC2_ROLE" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam attach-role-policy --role-name "$EC2_ROLE" --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly
aws iam put-role-policy --role-name "$EC2_ROLE" --policy-name read-app-params --policy-document "$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["ssm:GetParameter", "ssm:GetParameters"],
    "Resource": "arn:aws:ssm:$REGION:$ACCOUNT_ID:parameter/$PROJECT/*"
  }]
}
EOF
)"
if ! aws iam get-instance-profile --instance-profile-name "$EC2_ROLE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$EC2_ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$EC2_ROLE" --role-name "$EC2_ROLE"
  echo "created instance profile, waiting for IAM to propagate"
  sleep 15
fi

step "Security group"
VPC_ID=$(awsq ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
if [ "$VPC_ID" = "None" ]; then
  echo "No default VPC in $REGION. Create one with: aws ec2 create-default-vpc" >&2
  exit 1
fi
SG_ID=$(awsq ec2 describe-security-groups \
  --filters "Name=group-name,Values=$PROJECT-web" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG_ID" = "None" ]; then
  SG_ID=$(awsq ec2 create-security-group --group-name "$PROJECT-web" \
    --description "HTTP and HTTPS for $PROJECT" --vpc-id "$VPC_ID" --query GroupId --output text)
  for port in 80 443; do
    aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --protocol tcp --port "$port" --cidr 0.0.0.0/0 >/dev/null
  done
  echo "created $SG_ID (ports 80, 443; no SSH)"
else
  echo "exists  $SG_ID"
fi

step "EC2 instance"
INSTANCE_ID=$(awsq ec2 describe-instances \
  --filters "Name=tag:Project,Values=$PROJECT" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [ "$INSTANCE_ID" = "None" ]; then
  AMI_ID=$(awsq ssm get-parameter --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
    --query Parameter.Value --output text)
  # Retry because a freshly created instance profile can take a few seconds to become usable
  for attempt in 1 2 3 4 5; do
    if INSTANCE_ID=$(awsq ec2 run-instances \
      --image-id "$AMI_ID" \
      --instance-type "$INSTANCE_TYPE" \
      --iam-instance-profile "Name=$EC2_ROLE" \
      --security-group-ids "$SG_ID" \
      --user-data "$(cat "$SCRIPT_DIR/user-data.sh")" \
      --metadata-options HttpTokens=required,HttpEndpoint=enabled \
      --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]' \
      --tag-specifications \
        "ResourceType=instance,Tags=[{Key=Name,Value=$PROJECT},{Key=Project,Value=$PROJECT}]" \
        "ResourceType=volume,Tags=[{Key=Name,Value=$PROJECT},{Key=Project,Value=$PROJECT}]" \
      --query 'Instances[0].InstanceId' --output text); then
      break
    fi
    echo "launch failed (attempt $attempt), retrying in 10s"
    sleep 10
  done
  if [[ "$INSTANCE_ID" != i-* ]]; then
    echo "Could not launch the instance. See the AWS error above." >&2
    exit 1
  fi
  echo "launched $INSTANCE_ID ($INSTANCE_TYPE)"
else
  STATE=$(awsq ec2 describe-instances --instance-ids "$INSTANCE_ID" --query 'Reservations[0].Instances[0].State.Name' --output text)
  echo "exists  $INSTANCE_ID ($STATE)"
  if [ "$STATE" = "stopped" ]; then
    aws ec2 start-instances --instance-ids "$INSTANCE_ID" >/dev/null
    echo "started it"
  fi
fi
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"

step "Elastic IP"
ALLOC_ID=$(awsq ec2 describe-addresses --filters "Name=tag:Project,Values=$PROJECT" \
  --query 'Addresses[0].AllocationId' --output text)
if [ "$ALLOC_ID" = "None" ]; then
  ALLOC_ID=$(awsq ec2 allocate-address --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=$PROJECT},{Key=Project,Value=$PROJECT}]" \
    --query AllocationId --output text)
  echo "allocated $ALLOC_ID"
fi
aws ec2 associate-address --allocation-id "$ALLOC_ID" --instance-id "$INSTANCE_ID" >/dev/null
PUBLIC_IP=$(awsq ec2 describe-addresses --allocation-ids "$ALLOC_ID" --query 'Addresses[0].PublicIp' --output text)
# sslip.io resolves 1-2-3-4.sslip.io to 1.2.3.4, which gives Caddy a hostname for a free HTTPS certificate
APP_HOST="${PUBLIC_IP//./-}.sslip.io"
echo "public IP $PUBLIC_IP -> https://$APP_HOST"

step "SSM parameters"
put_param() {
  aws ssm put-parameter --name "/$PROJECT/$1" --type "$2" --value "$3" --overwrite >/dev/null
  echo "set /$PROJECT/$1"
}
put_param APP_HOST String "$APP_HOST"
if ! aws ssm get-parameter --name "/$PROJECT/POSTGRES_PASSWORD" >/dev/null 2>&1; then
  put_param POSTGRES_PASSWORD SecureString "$(openssl rand -hex 24)"
else
  echo "exists  /$PROJECT/POSTGRES_PASSWORD"
fi
if ! aws ssm get-parameter --name "/$PROJECT/ADMIN_TOKEN" >/dev/null 2>&1; then
  put_param ADMIN_TOKEN SecureString "$(openssl rand -hex 32)"
else
  echo "exists  /$PROJECT/ADMIN_TOKEN"
fi
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  put_param ANTHROPIC_API_KEY SecureString "$ANTHROPIC_API_KEY"
else
  echo "skipped /$PROJECT/ANTHROPIC_API_KEY (ANTHROPIC_API_KEY not set; the worker runs without an LLM)"
fi

step "GitHub OIDC provider and deploy role"
OIDC_ARN="arn:aws:iam::$ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"
if ! aws iam get-open-id-connect-provider --open-id-connect-provider-arn "$OIDC_ARN" >/dev/null 2>&1; then
  # AWS no longer checks this thumbprint for GitHub, but older CLI versions require one
  aws iam create-open-id-connect-provider \
    --url https://token.actions.githubusercontent.com \
    --client-id-list sts.amazonaws.com \
    --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1 >/dev/null
  echo "created OIDC provider"
else
  echo "exists  OIDC provider"
fi

GH_ROLE="$PROJECT-github-deploy"
# Only workflows running on the main branch of this repo can assume the role
ensure_role "$GH_ROLE" "$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "$OIDC_ARN"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:$GITHUB_REPO:ref:refs/heads/main"
      }
    }
  }]
}
EOF
)"
aws iam put-role-policy --role-name "$GH_ROLE" --policy-name deploy --policy-document "$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
    {
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"
      ],
      "Resource": "arn:aws:ecr:$REGION:$ACCOUNT_ID:repository/$PROJECT-*"
    },
    {
      "Effect": "Allow",
      "Action": "ssm:SendCommand",
      "Resource": [
        "arn:aws:ec2:$REGION:$ACCOUNT_ID:instance/$INSTANCE_ID",
        "arn:aws:ssm:$REGION::document/AWS-RunShellScript"
      ]
    },
    {"Effect": "Allow", "Action": "ssm:GetCommandInvocation", "Resource": "*"}
  ]
}
EOF
)"
GH_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$GH_ROLE"

step "Budget alert"
if [ -n "$ALERT_EMAIL" ]; then
  if ! aws budgets describe-budget --account-id "$ACCOUNT_ID" --budget-name "$PROJECT-monthly" >/dev/null 2>&1; then
    aws budgets create-budget --account-id "$ACCOUNT_ID" \
      --budget "{\"BudgetName\":\"$PROJECT-monthly\",\"BudgetLimit\":{\"Amount\":\"$BUDGET_USD\",\"Unit\":\"USD\"},\"TimeUnit\":\"MONTHLY\",\"BudgetType\":\"COST\"}" \
      --notifications-with-subscribers "[
        {\"Notification\":{\"NotificationType\":\"ACTUAL\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":80,\"ThresholdType\":\"PERCENTAGE\"},
         \"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$ALERT_EMAIL\"}]},
        {\"Notification\":{\"NotificationType\":\"FORECASTED\",\"ComparisonOperator\":\"GREATER_THAN\",\"Threshold\":100,\"ThresholdType\":\"PERCENTAGE\"},
         \"Subscribers\":[{\"SubscriptionType\":\"EMAIL\",\"Address\":\"$ALERT_EMAIL\"}]}
      ]"
    echo "created budget of $BUDGET_USD USD/month, alerts to $ALERT_EMAIL"
  else
    echo "exists  budget $PROJECT-monthly"
  fi
else
  echo "skipped (set ALERT_EMAIL to get an email before spend passes $BUDGET_USD USD/month)"
fi

step "Waiting for the instance to register with SSM (needed for deploys)"
for _ in $(seq 1 30); do
  PING=$(awsq ssm describe-instance-information --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query 'InstanceInformationList[0].PingStatus' --output text)
  [ "$PING" = "Online" ] && break
  sleep 10
done
echo "SSM status: $PING"

cat <<EOF

Done. Add these in GitHub (Settings > Secrets and variables > Actions).

Secrets tab > New repository secret:

  AWS_ROLE_ARN      $GH_ROLE_ARN

Variables tab > New repository variable:

  AWS_REGION        $REGION
  EC2_INSTANCE_ID   $INSTANCE_ID
  APP_HOST          $APP_HOST

Then push to main (or run the Deploy workflow by hand) and open https://$APP_HOST
Images are pushed to $REGISTRY
EOF
