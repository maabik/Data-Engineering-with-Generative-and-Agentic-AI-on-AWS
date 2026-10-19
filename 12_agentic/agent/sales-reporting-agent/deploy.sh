#!/usr/bin/env bash
# Sales Reporting Agent — single-shot deploy (shell alternative to the three
# manual steps in the README: execution role, Cognito + secret, agentcore launch).
#
# Provisions, idempotently:
#   - IAM role     SalesAgentExecutionRole
#   - Cognito      SalesAgentUserPool + SalesAgentClient + user
#   - Secrets      sales-agent-credentials  (Cognito + Redshift reader credentials)
#   - ECR repo     sales-reporting-agent + container build/push (linux/arm64)
#   - AgentCore    sales_reporting_agent (Runtime, JWT authorizer, Authorization header passthrough)
#
# Prerequisites (see README): the Redshift MCP server (SSM /mcp/redshift/agent_arn,
# secret /mcp/redshift/credentials) and the SQL Evaluations Agent
# (SSM /sql-evaluations-agent/gateway_url, secret sql-evaluations-agent/gateway-credentials).
#
# Required env:  COGNITO_PASSWORD, REDSHIFT_PASSWORD
# Optional env:  COGNITO_USERNAME (default salesagent), REDSHIFT_USERNAME (default sales_reader),
#                SALES_MEMORY_ID, AWS_REGION, CONTAINER_PLATFORM, LOCAL_REGISTRY
set -euo pipefail

: "${COGNITO_PASSWORD:?Set COGNITO_PASSWORD before running this script}"
: "${REDSHIFT_PASSWORD:?Set REDSHIFT_PASSWORD before running this script}"

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-sales-reporting-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

RT_ROLE="SalesAgentExecutionRole"
USER_POOL_NAME="SalesAgentUserPool"
APP_CLIENT_NAME="SalesAgentClient"
COGNITO_USERNAME="${COGNITO_USERNAME:-salesagent}"
REDSHIFT_USERNAME="${REDSHIFT_USERNAME:-sales_reader}"
SECRET_NAME="sales-agent-credentials"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="sales_reporting_agent"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

echo "[sales-reporting-agent] deploy region=$REGION account=$ACCOUNT"

# ─── 0. Prerequisites ───────────────────────────────────────────────────────
missing=0
for p in /mcp/redshift/agent_arn /sql-evaluations-agent/gateway_url; do
  aws ssm get-parameter --name "$p" --region "$REGION" >/dev/null 2>&1 || { echo "ERROR: missing SSM parameter $p" >&2; missing=1; }
done
for s in /mcp/redshift/credentials sql-evaluations-agent/gateway-credentials; do
  aws secretsmanager describe-secret --secret-id "$s" --region "$REGION" >/dev/null 2>&1 || { echo "ERROR: missing secret $s" >&2; missing=1; }
done
[ "$missing" = 0 ] || { echo "Deploy the Redshift MCP server and the SQL Evaluations Agent first." >&2; exit 1; }

# ─── 1. IAM execution role ──────────────────────────────────────────────────
RT_TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT}"},"ArnLike":{"aws:SourceArn":"arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT}:*"}}}]}
JSON
)
if ! aws iam get-role --role-name "$RT_ROLE" >/dev/null 2>&1; then
  echo "  create role $RT_ROLE"
  aws iam create-role --role-name "$RT_ROLE" --assume-role-policy-document "$RT_TRUST" \
    --description "Execution role for Sales Agent on AgentCore runtime" >/dev/null
fi
RT_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${RT_ROLE}"

RT_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogStreams","logs:DescribeLogGroups"],"Resource":"*"},
  {"Effect":"Allow","Action":["ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:BatchCheckLayerAvailability","ecr:GetAuthorizationToken"],"Resource":"*"},
  {"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],"Resource":"*"},
  {"Effect":"Allow","Action":["secretsmanager:GetSecretValue"],"Resource":["arn:aws:secretsmanager:${REGION}:*:secret:sales-agent-credentials*","arn:aws:secretsmanager:${REGION}:*:secret:/mcp/redshift/credentials*","arn:aws:secretsmanager:${REGION}:*:secret:sql-evaluations-agent/gateway-credentials*"]},
  {"Effect":"Allow","Action":["ssm:GetParameter"],"Resource":["arn:aws:ssm:${REGION}:${ACCOUNT}:parameter/mcp/redshift/*","arn:aws:ssm:${REGION}:${ACCOUNT}:parameter/sql-evaluations-agent/*"]},
  {"Effect":"Allow","Action":["bedrock-agentcore:*"],"Resource":"*"},
  {"Effect":"Allow","Action":["cognito-idp:InitiateAuth"],"Resource":"*"},
  {"Effect":"Allow","Action":["xray:PutTraceSegments","xray:PutTelemetryRecords","xray:GetSamplingRules","xray:GetSamplingTargets","xray:GetSamplingStatisticSummaries"],"Resource":"*"}
]}
JSON
)
aws iam put-role-policy --role-name "$RT_ROLE" --policy-name inline --policy-document "$RT_POLICY" >/dev/null

# ─── 2. Cognito user pool, client, user ─────────────────────────────────────
USER_POOL_ID="$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" --query "UserPools[?Name=='${USER_POOL_NAME}'].Id | [0]" --output text 2>/dev/null || true)"
if [ -z "$USER_POOL_ID" ] || [ "$USER_POOL_ID" = "None" ]; then
  echo "  create user pool $USER_POOL_NAME"
  USER_POOL_ID="$(aws cognito-idp create-user-pool --pool-name "$USER_POOL_NAME" --region "$REGION" \
    --policies '{"PasswordPolicy":{"MinimumLength":8,"RequireUppercase":true,"RequireLowercase":true,"RequireNumbers":true}}' \
    --admin-create-user-config AllowAdminCreateUserOnly=true \
    --query 'UserPool.Id' --output text)"
fi
CLIENT_ID="$(aws cognito-idp list-user-pool-clients --user-pool-id "$USER_POOL_ID" --region "$REGION" \
  --query "UserPoolClients[?ClientName=='${APP_CLIENT_NAME}'].ClientId | [0]" --output text 2>/dev/null || true)"
if [ -z "$CLIENT_ID" ] || [ "$CLIENT_ID" = "None" ]; then
  echo "  create app client $APP_CLIENT_NAME"
  CLIENT_ID="$(aws cognito-idp create-user-pool-client --user-pool-id "$USER_POOL_ID" --client-name "$APP_CLIENT_NAME" \
    --no-generate-secret --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --region "$REGION" --query 'UserPoolClient.ClientId' --output text)"
fi
if ! aws cognito-idp admin-get-user --user-pool-id "$USER_POOL_ID" --username "$COGNITO_USERNAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  create user $COGNITO_USERNAME"
  aws cognito-idp admin-create-user --user-pool-id "$USER_POOL_ID" --username "$COGNITO_USERNAME" \
    --temporary-password "$COGNITO_PASSWORD" --message-action SUPPRESS --region "$REGION" >/dev/null
fi
aws cognito-idp admin-set-user-password --user-pool-id "$USER_POOL_ID" --username "$COGNITO_USERNAME" \
  --password "$COGNITO_PASSWORD" --permanent --region "$REGION" >/dev/null
DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"

# ─── 3. Secret (Cognito + Redshift reader credentials) ──────────────────────
SECRET_PAYLOAD=$(jq -nc \
  --arg pool "$USER_POOL_ID" --arg cid "$CLIENT_ID" --arg cu "$COGNITO_USERNAME" \
  --arg cp "$COGNITO_PASSWORD" --arg region "$REGION" \
  --arg ru "$REDSHIFT_USERNAME" --arg rp "$REDSHIFT_PASSWORD" \
  '{cognito:{user_pool_id:$pool, client_id:$cid, username:$cu, password:$cp, region:$region},
    redshift:{cluster_identifier:"sales-demo-cluster", database_name:"salesdb", username:$ru, password:$rp}}')
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws secretsmanager put-secret-value --secret-id "$SECRET_NAME" --secret-string "$SECRET_PAYLOAD" --region "$REGION" >/dev/null
else
  aws secretsmanager create-secret --name "$SECRET_NAME" --description "Credentials for Sales Reporting Agent" \
    --secret-string "$SECRET_PAYLOAD" --region "$REGION" >/dev/null
fi

# ─── 4. ECR repo + container build ──────────────────────────────────────────
if ! aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$REGION" >/dev/null 2>&1; then
  echo "  create ecr repo $ECR_REPO"
  aws ecr create-repository --repository-name "$ECR_REPO" --region "$REGION" >/dev/null
fi
ECR_URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}"
ECR_HOST="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
# Use the repository URI the ECR API reports (floci returns <acct>.dkr.ecr.<region>.localhost:4566/<repo>)
REPO_URI="$(aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$REGION" --query 'repositories[0].repositoryUri' --output text 2>/dev/null || true)"
if [ -n "$REPO_URI" ] && [ "$REPO_URI" != "None" ]; then ECR_URI="$REPO_URI"; ECR_HOST="${REPO_URI%%/*}"; fi
# Local dev (e.g. floci): set LOCAL_REGISTRY=localhost:5000 to push to a plain local registry instead of ECR
if [ -n "${LOCAL_REGISTRY:-}" ]; then ECR_HOST="$LOCAL_REGISTRY"; ECR_URI="$LOCAL_REGISTRY/$ECR_REPO"; fi

if command -v docker >/dev/null 2>&1; then
  RUNTIME=docker
elif command -v finch >/dev/null 2>&1; then
  RUNTIME=finch
else
  echo "ERROR: need docker or finch installed" >&2; exit 1
fi
echo "  container runtime: $RUNTIME, push -> $ECR_URI"
[ -n "${LOCAL_REGISTRY:-}" ] || aws ecr get-login-password --region "$REGION" | "$RUNTIME" login --username AWS --password-stdin "$ECR_HOST" >/dev/null

# Build context: only what the image needs
cp "$HERE/Dockerfile" "$HERE/requirements.txt" "$HERE/sales_reporting_agent.py" "$HERE/sales_memory_hooks.py" "$BUILD/"

if [ "$RUNTIME" = "docker" ] && docker --version 2>&1 | grep -qi podman; then
  # docker is an alias for podman, which has no `buildx use`
  docker build --platform "${CONTAINER_PLATFORM:-linux/$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')}" -t "${ECR_URI}:latest" "$BUILD" >&2
  docker push "${ECR_URI}:latest" >&2
elif [ "$RUNTIME" = "docker" ]; then
  docker buildx inspect sales-reporting-agent-builder >/dev/null 2>&1 || docker buildx create --name sales-reporting-agent-builder --use >/dev/null
  docker buildx use sales-reporting-agent-builder >/dev/null
  docker buildx build --platform "${CONTAINER_PLATFORM:-linux/arm64}" -t "${ECR_URI}:latest" --push "$BUILD" >&2
else
  finch build --platform "${CONTAINER_PLATFORM:-linux/arm64}" -t "${ECR_URI}:latest" "$BUILD" >&2
  finch push "${ECR_URI}:latest" >&2
fi

# ─── 5. AgentCore Runtime (Cognito JWT authorizer, Authorization header passed through) ──
AUTH_CONFIG=$(jq -nc --arg discovery "$DISCOVERY_URL" --arg cid "$CLIENT_ID" \
  '{customJWTAuthorizer:{discoveryUrl:$discovery, allowedClients:[$cid]}}')
HEADER_CONFIG='{"requestHeaderAllowlist":["Authorization"]}'
RT_ENV_VARS=$(jq -nc --arg mem "${SALES_MEMORY_ID:-}" \
  'if $mem == "" then {} else {SALES_MEMORY_ID:$mem} end')
CONTAINER_URI="${ECR_URI}:latest"

EXISTING_RT="$(aws bedrock-agentcore-control list-agent-runtimes --no-paginate --region "$REGION" --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" --output text 2>/dev/null || true)"

if [ -n "$EXISTING_RT" ] && [ "$EXISTING_RT" != "None" ]; then
  echo "  update agent runtime $RUNTIME_NAME"
  aws bedrock-agentcore-control update-agent-runtime --region "$REGION" \
    --agent-runtime-id "$EXISTING_RT" \
    --role-arn "$RT_ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --authorizer-configuration "$AUTH_CONFIG" \
    --request-header-configuration "$HEADER_CONFIG" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"$CONTAINER_URI\"}}" \
    --environment-variables "$RT_ENV_VARS" >/dev/null
  RUNTIME_ID="$EXISTING_RT"
else
  echo "  create agent runtime $RUNTIME_NAME"
  RUNTIME_ID="$(aws bedrock-agentcore-control create-agent-runtime --region "$REGION" \
    --agent-runtime-name "$RUNTIME_NAME" \
    --role-arn "$RT_ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --authorizer-configuration "$AUTH_CONFIG" \
    --request-header-configuration "$HEADER_CONFIG" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"$CONTAINER_URI\"}}" \
    --environment-variables "$RT_ENV_VARS" \
    --query 'agentRuntimeId' --output text)"
fi

for i in $(seq 1 60); do
  s="$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region "$REGION" --query 'status' --output text)"
  echo "  runtime status: $s ($i)"
  [ "$s" = "READY" ] && break
  sleep 5
done

RUNTIME_ARN="$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region "$REGION" --query 'agentRuntimeArn' --output text)"

printf '\n✅ sales-reporting-agent deployed\n\n'
printf '  region:            %s\n' "$REGION"
printf '  user pool:         %s\n' "$USER_POOL_ID"
printf '  client id:         %s\n' "$CLIENT_ID"
printf '  agent runtime ARN: %s\n' "$RUNTIME_ARN"
printf '  credentials:       secret %s\n' "$SECRET_NAME"
