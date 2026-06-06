#!/usr/bin/env bash
# SQL Evaluations Agent — single-shot deploy.
#
# Provisions, idempotently:
#   - S3 bucket    sql-evaluations-agent-${ACCOUNT}-${REGION}  (steering doc)
#   - IAM roles    sql-evaluations-agent-runtime, -lambda, -gateway
#   - Cognito      user pool + resource server + app client + test user
#   - Secrets      sql-evaluations-agent/gateway-credentials
#   - Lambda       sql-evaluations-agent-tools          (Gateway target)
#   - Gateway      sql-evaluations-agent-gw + Lambda target with toolSchema
#   - ECR repo     sql-evaluations-agent + container build/push (linux/arm64)
#   - AgentCore    sql_evaluations_agent (Runtime)
#
# Skips AIDLC ceremony. Accepts all defaults.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-sql-evaluations-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

STEERING_BUCKET="${STEERING_BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"
STEERING_KEY="${STEERING_KEY:-steering-doc.md}"

LAMBDA_NAME="${PREFIX}-tools"
LAMBDA_ROLE="${PREFIX}-lambda-role"
GW_ROLE="${PREFIX}-gateway-role"
RT_ROLE="${PREFIX}-runtime-role"
GW_NAME="${PREFIX}-gw"
GW_TARGET_NAME="evaluations"
USER_POOL_NAME="${PREFIX}-pool"
RESOURCE_SERVER_ID="${PREFIX}-resource"
APP_CLIENT_NAME="${PREFIX}-client"
SECRET_NAME="${PREFIX}/gateway-credentials"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="sql_evaluations_agent"
RUNTIME_USER="${PREFIX}-user"
RUNTIME_PASSWORD="${RUNTIME_PASSWORD:-SqlEvaluationsAgentPass$(date +%s)!}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-sonnet-4-6}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

echo "[sql-evaluations-agent] deploy region=$REGION account=$ACCOUNT bucket=$STEERING_BUCKET"

# ─── 1. S3 steering bucket ──────────────────────────────────────────────────
if ! aws s3api head-bucket --bucket "$STEERING_BUCKET" --region "$REGION" >/dev/null 2>&1; then
  echo "  create steering bucket $STEERING_BUCKET"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$STEERING_BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$STEERING_BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  fi
fi
aws s3api put-bucket-versioning --bucket "$STEERING_BUCKET" --region "$REGION" \
  --versioning-configuration Status=Enabled >/dev/null

# ─── 2. IAM roles ───────────────────────────────────────────────────────────
ensure_role() {
  local name="$1" trust="$2"
  if ! aws iam get-role --role-name "$name" >/dev/null 2>&1; then
    echo "  create role $name"
    aws iam create-role --role-name "$name" --assume-role-policy-document "$trust" >/dev/null
  fi
}

LAMBDA_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
GW_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
RT_TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT}"},"ArnLike":{"aws:SourceArn":"arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT}:*"}}}]}
JSON
)

ensure_role "$LAMBDA_ROLE" "$LAMBDA_TRUST"
ensure_role "$GW_ROLE"     "$GW_TRUST"
ensure_role "$RT_ROLE"     "$RT_TRUST"

LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${LAMBDA_ROLE}"
GW_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${GW_ROLE}"
RT_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${RT_ROLE}"

LAMBDA_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"},
  {"Effect":"Allow","Action":["bedrock-agentcore:InvokeAgentRuntime"],"Resource":"*"}
]}
JSON
)
GW_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["lambda:InvokeFunction"],"Resource":"arn:aws:lambda:${REGION}:${ACCOUNT}:function:${LAMBDA_NAME}"}
]}
JSON
)
RT_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogStreams","logs:DescribeLogGroups"],"Resource":"*"},
  {"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],"Resource":"*"},
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:ListBucket"],"Resource":["arn:aws:s3:::${STEERING_BUCKET}","arn:aws:s3:::${STEERING_BUCKET}/*"]},
  {"Effect":"Allow","Action":["ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:BatchCheckLayerAvailability","ecr:GetAuthorizationToken"],"Resource":"*"},
  {"Effect":"Allow","Action":["xray:PutTraceSegments","xray:PutTelemetryRecords","xray:GetSamplingRules","xray:GetSamplingTargets","xray:GetSamplingStatisticSummaries"],"Resource":"*"}
]}
JSON
)

aws iam put-role-policy --role-name "$LAMBDA_ROLE" --policy-name inline --policy-document "$LAMBDA_POLICY" >/dev/null
aws iam put-role-policy --role-name "$GW_ROLE"     --policy-name inline --policy-document "$GW_POLICY"     >/dev/null
aws iam put-role-policy --role-name "$RT_ROLE"     --policy-name inline --policy-document "$RT_POLICY"     >/dev/null

# Wait for IAM propagation before downstream services try to assume the roles.
sleep 8

# ─── 3. Cognito user pool + resource server + app client + test user ────────
USER_POOL_ID="$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" --query "UserPools[?Name=='${USER_POOL_NAME}'].Id | [0]" --output text 2>/dev/null || true)"
if [ -z "$USER_POOL_ID" ] || [ "$USER_POOL_ID" = "None" ]; then
  echo "  create user pool $USER_POOL_NAME"
  USER_POOL_ID="$(aws cognito-idp create-user-pool --pool-name "$USER_POOL_NAME" --region "$REGION" \
    --policies '{"PasswordPolicy":{"MinimumLength":8,"RequireUppercase":true,"RequireLowercase":true,"RequireNumbers":true,"RequireSymbols":true}}' \
    --admin-create-user-config AllowAdminCreateUserOnly=true \
    --query 'UserPool.Id' --output text)"
  aws cognito-idp create-resource-server --user-pool-id "$USER_POOL_ID" \
    --identifier "$RESOURCE_SERVER_ID" --name "$RESOURCE_SERVER_ID" \
    --scopes 'ScopeName=invoke,ScopeDescription=invoke gateway' --region "$REGION" >/dev/null
fi

CLIENT_ID="$(aws cognito-idp list-user-pool-clients --user-pool-id "$USER_POOL_ID" --region "$REGION" \
  --query "UserPoolClients[?ClientName=='${APP_CLIENT_NAME}'].ClientId | [0]" --output text 2>/dev/null || true)"
if [ -z "$CLIENT_ID" ] || [ "$CLIENT_ID" = "None" ]; then
  echo "  create app client $APP_CLIENT_NAME"
  CLIENT_ID="$(aws cognito-idp create-user-pool-client --user-pool-id "$USER_POOL_ID" --client-name "$APP_CLIENT_NAME" \
    --explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH \
    --region "$REGION" --query 'UserPoolClient.ClientId' --output text)"
fi

if ! aws cognito-idp admin-get-user --user-pool-id "$USER_POOL_ID" --username "$RUNTIME_USER" --region "$REGION" >/dev/null 2>&1; then
  echo "  create test user $RUNTIME_USER"
  aws cognito-idp admin-create-user --user-pool-id "$USER_POOL_ID" --username "$RUNTIME_USER" \
    --temporary-password "$RUNTIME_PASSWORD" --message-action SUPPRESS --region "$REGION" >/dev/null
fi
aws cognito-idp admin-set-user-password --user-pool-id "$USER_POOL_ID" --username "$RUNTIME_USER" \
  --password "$RUNTIME_PASSWORD" --permanent --region "$REGION" >/dev/null

DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"

SECRET_PAYLOAD=$(jq -nc --arg cid "$CLIENT_ID" --arg user "$RUNTIME_USER" --arg pw "$RUNTIME_PASSWORD" --arg pool "$USER_POOL_ID" \
  '{client_id:$cid, username:$user, password:$pw, user_pool_id:$pool}')
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws secretsmanager update-secret --secret-id "$SECRET_NAME" --secret-string "$SECRET_PAYLOAD" --region "$REGION" >/dev/null
else
  aws secretsmanager create-secret --name "$SECRET_NAME" --secret-string "$SECRET_PAYLOAD" --region "$REGION" >/dev/null
fi

# ─── 4. Tools Lambda (gateway target) ───────────────────────────────────────
echo "  package tools lambda"
cp "$HERE/tools/lambda_function.py" "$BUILD/lambda_function.py"
(cd "$BUILD" && zip -q lambda.zip lambda_function.py)

# Created with a placeholder ARN; we'll patch in the real runtime ARN once it exists.
TMP_ENV='{"Variables":{"AGENT_RUNTIME_ARN":"placeholder"}}'

if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  update tools lambda code"
  aws lambda update-function-code --function-name "$LAMBDA_NAME" --region "$REGION" --zip-file "fileb://$BUILD/lambda.zip" >/dev/null
  aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$REGION"
else
  echo "  create tools lambda"
  for i in 1 2 3 4 5 6 7 8; do
    if aws lambda create-function --function-name "$LAMBDA_NAME" --region "$REGION" \
        --runtime python3.12 --role "$LAMBDA_ROLE_ARN" \
        --handler lambda_function.lambda_handler \
        --zip-file "fileb://$BUILD/lambda.zip" \
        --timeout 300 --memory-size 256 \
        --environment "$TMP_ENV" >/dev/null 2>/tmp/lc.err; then
      break
    fi
    if grep -q "cannot be assumed" /tmp/lc.err; then
      echo "    waiting for IAM... ($i)"; sleep 5; continue
    fi
    cat /tmp/lc.err >&2; exit 1
  done
fi
LAMBDA_ARN="$(aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" --query 'Configuration.FunctionArn' --output text)"

# ─── 5. AgentCore Gateway + Lambda target ───────────────────────────────────
GW_ID="$(aws bedrock-agentcore-control list-gateways --region "$REGION" --query "items[?name=='${GW_NAME}'].gatewayId | [0]" --output text 2>/dev/null || true)"
if [ -z "$GW_ID" ] || [ "$GW_ID" = "None" ]; then
  echo "  create gateway $GW_NAME"
  GW_PAYLOAD=$(jq -nc --arg name "$GW_NAME" --arg role "$GW_ROLE_ARN" --arg discovery "$DISCOVERY_URL" --arg cid "$CLIENT_ID" \
    '{name:$name, roleArn:$role, protocolType:"MCP", authorizerType:"CUSTOM_JWT",
      authorizerConfiguration:{customJWTAuthorizer:{discoveryUrl:$discovery, allowedClients:[$cid]}}}')
  GW_ID="$(aws bedrock-agentcore-control create-gateway --region "$REGION" --cli-input-json "$GW_PAYLOAD" --query 'gatewayId' --output text)"
  for i in 1 2 3 4 5 6 7 8 9 10; do
    s="$(aws bedrock-agentcore-control get-gateway --gateway-identifier "$GW_ID" --region "$REGION" --query 'status' --output text)"
    [ "$s" = "READY" ] && break
    sleep 3
  done
fi
GW_URL="$(aws bedrock-agentcore-control get-gateway --gateway-identifier "$GW_ID" --region "$REGION" --query 'gatewayUrl' --output text)"

TOOL_SCHEMA=$(cat <<'JSON'
[
  {
    "name": "evaluate_sql",
    "description": "Independent SQL evaluator. Reviews a SQL statement for errors, optimizations, and security risks as a separation-of-concerns check. Returns status (approved|revised|rejected), findings grouped by concern, an optional revised_sql, and a short reasoning string.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "sql":     {"type": "string", "description": "SQL statement to evaluate."},
        "context": {"type": "string", "description": "Optional intent/context (table sizes, schema notes, intended operation)."},
        "dialect": {"type": "string", "description": "SQL dialect: ansi, redshift, postgres, snowflake, athena, bigquery, etc. Defaults to ansi."}
      },
      "required": ["sql"]
    }
  }
]
JSON
)

EXISTING_TARGET="$(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --region "$REGION" --query "items[?name=='${GW_TARGET_NAME}'].targetId | [0]" --output text 2>/dev/null || true)"
if [ -n "$EXISTING_TARGET" ] && [ "$EXISTING_TARGET" != "None" ]; then
  echo "  update gateway target $GW_TARGET_NAME"
  UPDATE_PAYLOAD=$(jq -nc --arg gw "$GW_ID" --arg tid "$EXISTING_TARGET" --arg name "$GW_TARGET_NAME" --arg larn "$LAMBDA_ARN" --argjson tools "$TOOL_SCHEMA" \
    '{gatewayIdentifier:$gw, targetId:$tid, name:$name, description:"sql-evaluations-agent tools",
      targetConfiguration:{mcp:{lambda:{lambdaArn:$larn, toolSchema:{inlinePayload:$tools}}}},
      credentialProviderConfigurations:[{credentialProviderType:"GATEWAY_IAM_ROLE"}]}')
  aws bedrock-agentcore-control update-gateway-target --region "$REGION" --cli-input-json "$UPDATE_PAYLOAD" >/dev/null
else
  echo "  create gateway target $GW_TARGET_NAME"
  TARGET_PAYLOAD=$(jq -nc --arg gw "$GW_ID" --arg name "$GW_TARGET_NAME" --arg larn "$LAMBDA_ARN" --argjson tools "$TOOL_SCHEMA" \
    '{gatewayIdentifier:$gw, name:$name, description:"sql-evaluations-agent tools",
      targetConfiguration:{mcp:{lambda:{lambdaArn:$larn, toolSchema:{inlinePayload:$tools}}}},
      credentialProviderConfigurations:[{credentialProviderType:"GATEWAY_IAM_ROLE"}]}')
  aws bedrock-agentcore-control create-gateway-target --region "$REGION" --cli-input-json "$TARGET_PAYLOAD" >/dev/null
fi

aws lambda add-permission --function-name "$LAMBDA_NAME" --region "$REGION" \
  --statement-id agentcore-gw-invoke --action lambda:InvokeFunction \
  --principal bedrock-agentcore.amazonaws.com >/dev/null 2>&1 || true

# ─── 6. ECR repo + container build (linux/arm64) ────────────────────────────
if ! aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$REGION" >/dev/null 2>&1; then
  echo "  create ecr repo $ECR_REPO"
  aws ecr create-repository --repository-name "$ECR_REPO" --region "$REGION" >/dev/null
fi
ECR_URI="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}"
ECR_HOST="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"

if command -v docker >/dev/null 2>&1; then
  RUNTIME=docker
elif command -v finch >/dev/null 2>&1; then
  RUNTIME=finch
else
  echo "ERROR: need docker or finch installed" >&2; exit 1
fi
echo "  container runtime: $RUNTIME, push -> $ECR_URI"
aws ecr get-login-password --region "$REGION" | "$RUNTIME" login --username AWS --password-stdin "$ECR_HOST" >/dev/null

if [ "$RUNTIME" = "docker" ]; then
  docker buildx inspect sql-evaluations-agent-builder >/dev/null 2>&1 || docker buildx create --name sql-evaluations-agent-builder --use >/dev/null
  docker buildx use sql-evaluations-agent-builder >/dev/null
  docker buildx build --platform linux/arm64 -t "${ECR_URI}:latest" --push "$HERE/agent" >&2
else
  finch build --platform linux/arm64 -t "${ECR_URI}:latest" "$HERE/agent" >&2
  finch push "${ECR_URI}:latest" >&2
fi

# ─── 7. AgentCore Runtime ───────────────────────────────────────────────────
RT_ENV_VARS=$(jq -nc \
  --arg bucket "$STEERING_BUCKET" --arg key "$STEERING_KEY" \
  --arg model "$MODEL_ID" --arg region "$REGION" \
  '{STEERING_BUCKET:$bucket, STEERING_KEY:$key, MODEL_ID:$model, AWS_REGION:$region}')

EXISTING_RT="$(aws bedrock-agentcore-control list-agent-runtimes --no-paginate --region "$REGION" --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" --output text 2>/dev/null || true)"

CONTAINER_URI="${ECR_URI}:latest"

if [ -n "$EXISTING_RT" ] && [ "$EXISTING_RT" != "None" ]; then
  echo "  update agent runtime $RUNTIME_NAME"
  aws bedrock-agentcore-control update-agent-runtime --region "$REGION" \
    --agent-runtime-id "$EXISTING_RT" \
    --role-arn "$RT_ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
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

# ─── 8. Patch Lambda env with real runtime ARN ──────────────────────────────
LAMBDA_ENV=$(jq -nc --arg arn "$RUNTIME_ARN" '{Variables:{AGENT_RUNTIME_ARN:$arn}}')
aws lambda update-function-configuration --function-name "$LAMBDA_NAME" --region "$REGION" \
  --environment "$LAMBDA_ENV" >/dev/null
aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$REGION"

# ─── 9. Seed steering doc ───────────────────────────────────────────────────
STEERING_BUCKET="$STEERING_BUCKET" STEERING_KEY="$STEERING_KEY" AWS_REGION="$REGION" \
  python3 "$HERE/scripts/seed_steering_doc.py" || true

# ─── 10. SSM Parameter for downstream callers ───────────────────────────────
aws ssm put-parameter --name "/sql-evaluations-agent/agent_runtime_arn" --value "$RUNTIME_ARN" --type String --overwrite --region "$REGION" >/dev/null
aws ssm put-parameter --name "/sql-evaluations-agent/gateway_url"       --value "$GW_URL"      --type String --overwrite --region "$REGION" >/dev/null

printf '\n✅ sql-evaluations-agent deployed\n\n'
printf '  region:            %s\n' "$REGION"
printf '  steering bucket:   s3://%s/%s\n' "$STEERING_BUCKET" "$STEERING_KEY"
printf '  agent runtime ARN: %s\n' "$RUNTIME_ARN"
printf '  gateway URL:       %s\n' "$GW_URL"
printf '  gateway secret:    %s (Cognito creds for MCP auth)\n\n' "$SECRET_NAME"
printf 'Direct invoke:\n  AGENT_RUNTIME_ARN=%s python3 scripts/invoke_runtime.py "SELECT * FROM users"\n\n' "$RUNTIME_ARN"
printf 'MCP invoke (via Gateway):\n  GATEWAY_URL=%s SECRET_NAME=%s python3 scripts/invoke_via_gateway.py "DROP TABLE users"\n' "$GW_URL" "$SECRET_NAME"
