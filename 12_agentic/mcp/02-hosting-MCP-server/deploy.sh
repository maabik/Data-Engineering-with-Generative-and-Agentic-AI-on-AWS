#!/usr/bin/env bash
# Redshift MCP server — single-shot deploy (shell alternative to deploy_redshift_mcp.py).
#
# Provisions, idempotently:
#   - IAM role     redshift-mcp-server-runtime-role
#   - Cognito      RedshiftMCPUserPool + RedshiftMCPClient + user (same names as utils.py)
#   - ECR repo     redshift-mcp-server + container build/push (linux/arm64)
#   - AgentCore    redshift_mcp_server_agentcore (Runtime, protocol MCP, JWT authorizer)
#   - Secrets      /mcp/redshift/credentials
#   - SSM          /mcp/redshift/agent_arn
#
# Unlike deploy_redshift_mcp.py it does not use the AgentCore starter toolkit
# (CodeBuild + AgentCore endpoints that ignore AWS_ENDPOINT_URL); it builds the
# image locally and calls the control plane through the AWS CLI, so it also
# works against a local emulator (floci) and with podman.
#
# Required env:  COGNITO_PASSWORD  (permanent password for the pool user, min 8 chars)
# Optional env:  COGNITO_USERNAME (default redshiftuser), AWS_REGION, CONTAINER_PLATFORM,
#                LOCAL_REGISTRY
set -euo pipefail

if [ -z "${COGNITO_PASSWORD:-}" ]; then
  echo "ERROR: COGNITO_PASSWORD env var is required (refusing to use a hardcoded default)" >&2
  exit 1
fi

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-redshift-mcp-server}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

RT_ROLE="${PREFIX}-runtime-role"
USER_POOL_NAME="RedshiftMCPUserPool"
APP_CLIENT_NAME="RedshiftMCPClient"
COGNITO_USERNAME="${COGNITO_USERNAME:-redshiftuser}"
SECRET_NAME="/mcp/redshift/credentials"
ARN_PARAM="/mcp/redshift/agent_arn"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="redshift_mcp_server_agentcore"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

echo "[redshift-mcp-server] deploy region=$REGION account=$ACCOUNT"

# ─── 1. IAM runtime role ────────────────────────────────────────────────────
RT_TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT}"},"ArnLike":{"aws:SourceArn":"arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT}:*"}}}]}
JSON
)
if ! aws iam get-role --role-name "$RT_ROLE" >/dev/null 2>&1; then
  echo "  create role $RT_ROLE"
  aws iam create-role --role-name "$RT_ROLE" --assume-role-policy-document "$RT_TRUST" >/dev/null
fi
RT_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${RT_ROLE}"

RT_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogStreams","logs:DescribeLogGroups"],"Resource":"*"},
  {"Effect":"Allow","Action":["ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:BatchCheckLayerAvailability","ecr:GetAuthorizationToken"],"Resource":"*"},
  {"Effect":"Allow","Action":["redshift:DescribeClusters","redshift-serverless:ListWorkgroups","redshift-serverless:GetWorkgroup","redshift-data:ExecuteStatement","redshift-data:BatchExecuteStatement","redshift-data:DescribeStatement","redshift-data:GetStatementResult","redshift-data:CancelStatement"],"Resource":"*"},
  {"Effect":"Allow","Action":["redshift:GetClusterCredentials","redshift-serverless:GetCredentials","secretsmanager:GetSecretValue"],"Resource":"*"},
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
    --policies '{"PasswordPolicy":{"MinimumLength":8}}' \
    --admin-create-user-config '{"AllowAdminCreateUserOnly":true}' \
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

# ─── 3. ECR repo + container build ──────────────────────────────────────────
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

# Build context: only what the image needs (the folder also holds notebooks and images)
cp "$HERE/Dockerfile" "$HERE/requirements_redshift.txt" "$HERE/redshift_mcp_server.py" "$BUILD/"

if [ "$RUNTIME" = "docker" ] && docker --version 2>&1 | grep -qi podman; then
  # docker is an alias for podman, which has no `buildx use`
  docker build --platform "${CONTAINER_PLATFORM:-linux/$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')}" -t "${ECR_URI}:latest" "$BUILD" >&2
  docker push "${ECR_URI}:latest" >&2
elif [ "$RUNTIME" = "docker" ]; then
  docker buildx inspect redshift-mcp-server-builder >/dev/null 2>&1 || docker buildx create --name redshift-mcp-server-builder --use >/dev/null
  docker buildx use redshift-mcp-server-builder >/dev/null
  docker buildx build --platform "${CONTAINER_PLATFORM:-linux/arm64}" -t "${ECR_URI}:latest" --push "$BUILD" >&2
else
  finch build --platform "${CONTAINER_PLATFORM:-linux/arm64}" -t "${ECR_URI}:latest" "$BUILD" >&2
  finch push "${ECR_URI}:latest" >&2
fi

# ─── 4. AgentCore Runtime (MCP protocol, Cognito JWT authorizer) ────────────
AUTH_CONFIG=$(jq -nc --arg discovery "$DISCOVERY_URL" --arg cid "$CLIENT_ID" \
  '{customJWTAuthorizer:{discoveryUrl:$discovery, allowedClients:[$cid]}}')
CONTAINER_URI="${ECR_URI}:latest"

EXISTING_RT="$(aws bedrock-agentcore-control list-agent-runtimes --no-paginate --region "$REGION" --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" --output text 2>/dev/null || true)"

if [ -n "$EXISTING_RT" ] && [ "$EXISTING_RT" != "None" ]; then
  echo "  update agent runtime $RUNTIME_NAME"
  aws bedrock-agentcore-control update-agent-runtime --region "$REGION" \
    --agent-runtime-id "$EXISTING_RT" \
    --role-arn "$RT_ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"MCP"}' \
    --authorizer-configuration "$AUTH_CONFIG" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"$CONTAINER_URI\"}}" >/dev/null
  RUNTIME_ID="$EXISTING_RT"
else
  echo "  create agent runtime $RUNTIME_NAME"
  RUNTIME_ID="$(aws bedrock-agentcore-control create-agent-runtime --region "$REGION" \
    --agent-runtime-name "$RUNTIME_NAME" \
    --role-arn "$RT_ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"MCP"}' \
    --authorizer-configuration "$AUTH_CONFIG" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"$CONTAINER_URI\"}}" \
    --query 'agentRuntimeId' --output text)"
fi

for i in $(seq 1 60); do
  s="$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region "$REGION" --query 'status' --output text)"
  echo "  runtime status: $s ($i)"
  [ "$s" = "READY" ] && break
  sleep 5
done

RUNTIME_ARN="$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RUNTIME_ID" --region "$REGION" --query 'agentRuntimeArn' --output text)"

# ─── 5. Store configuration for remote access ───────────────────────────────
# Payload the sales agent expects: client_id / username / password.
# Bearer tokens are short-lived and minted on demand; they are not persisted.
SECRET_PAYLOAD=$(jq -nc \
  --arg pool "$USER_POOL_ID" --arg cid "$CLIENT_ID" --arg disc "$DISCOVERY_URL" \
  --arg user "$COGNITO_USERNAME" --arg pw "$COGNITO_PASSWORD" --arg region "$REGION" \
  '{user_pool_id:$pool, client_id:$cid, discovery_url:$disc, username:$user, password:$pw, region:$region}')
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws secretsmanager update-secret --secret-id "$SECRET_NAME" --secret-string "$SECRET_PAYLOAD" --region "$REGION" >/dev/null
else
  aws secretsmanager create-secret --name "$SECRET_NAME" --description "Cognito credentials for Redshift MCP server" \
    --secret-string "$SECRET_PAYLOAD" --region "$REGION" >/dev/null
fi
aws ssm put-parameter --name "$ARN_PARAM" --value "$RUNTIME_ARN" --type String --overwrite --region "$REGION" >/dev/null

printf '\n✅ redshift-mcp-server deployed\n\n'
printf '  region:            %s\n' "$REGION"
printf '  user pool:         %s\n' "$USER_POOL_ID"
printf '  client id:         %s\n' "$CLIENT_ID"
printf '  agent runtime ARN: %s\n' "$RUNTIME_ARN"
printf '  credentials:       secret %s\n' "$SECRET_NAME"
printf '  agent ARN param:   %s\n' "$ARN_PARAM"
