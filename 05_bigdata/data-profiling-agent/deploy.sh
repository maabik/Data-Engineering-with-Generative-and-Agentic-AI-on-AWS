#!/usr/bin/env bash
# Deploy data-profiling-agent.
#
# Creates:
#   - S3 bucket  ${BUCKET}                      (EventBridge enabled)
#   - IAM roles  data-profiling-agent-lambda, -bridge, -databrew, -gateway, -runtime
#   - Cognito    user pool + resource server + app client + test user
#   - Secrets    data-profiling-agent/gateway-credentials
#   - Lambda     data-profiling-agent-tools      (MCP tools target)
#   - Gateway    data-profiling-agent-gw + Lambda target with toolSchema
#   - ECR repo   data-profiling-agent + Docker build/push (linux/arm64)
#   - AgentCore  data_profiling_agent  (Strands agent runtime)
#   - Lambda     data-profiling-agent-bridge     (S3 -> runtime invoker)
#   - EventBridge rule on s3:ObjectCreated under landing/  -> bridge
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-data-profiling-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"
LANDING_PREFIX="${LANDING_PREFIX:-landing/}"
PROFILE_S3_PREFIX="${PROFILE_S3_PREFIX:-profile/}"
INSIGHTS_PREFIX="${INSIGHTS_PREFIX:-insights/}"

LAMBDA_NAME="${PREFIX}-tools"
BRIDGE_NAME="${PREFIX}-bridge"
LAMBDA_ROLE="${PREFIX}-lambda-role"
BRIDGE_ROLE="${PREFIX}-bridge-role"
DATABREW_ROLE="${PREFIX}-databrew-role"
GW_ROLE="${PREFIX}-gateway-role"
RT_ROLE="${PREFIX}-runtime-role"
GW_NAME="${PREFIX}-gw"
GW_TARGET_NAME="tools"
USER_POOL_NAME="${PREFIX}-pool"
RESOURCE_SERVER_ID="${PREFIX}-resource"
APP_CLIENT_NAME="${PREFIX}-client"
SECRET_NAME="${PREFIX}/gateway-credentials"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="data_profiling_agent"
RUNTIME_USER="${PREFIX}-user"
RUNTIME_PASSWORD="${RUNTIME_PASSWORD:-DataProfilingAgentPass$(date +%s)!}"
EB_RULE_NAME="${PREFIX}-landing-rule"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-sonnet-4-6}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

echo "[data-profiling-agent] deploy region=$REGION account=$ACCOUNT bucket=$BUCKET"

# ─── 1. S3 bucket (with EventBridge notifications) ──────────────────────────
if ! aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null 2>&1; then
  echo "  create bucket $BUCKET"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  fi
fi
aws s3api put-bucket-notification-configuration --bucket "$BUCKET" --region "$REGION" \
  --notification-configuration '{"EventBridgeConfiguration":{}}' >/dev/null

# ─── 2. IAM roles ───────────────────────────────────────────────────────────
ensure_role() {
  local name="$1" trust="$2"
  if ! aws iam get-role --role-name "$name" >/dev/null 2>&1; then
    echo "  create role $name"
    aws iam create-role --role-name "$name" --assume-role-policy-document "$trust" >/dev/null
  fi
}

LAMBDA_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
DATABREW_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"databrew.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
GW_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
RT_TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT}"},"ArnLike":{"aws:SourceArn":"arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT}:*"}}}]}
JSON
)

ensure_role "$LAMBDA_ROLE"   "$LAMBDA_TRUST"
ensure_role "$BRIDGE_ROLE"   "$LAMBDA_TRUST"
ensure_role "$DATABREW_ROLE" "$DATABREW_TRUST"
ensure_role "$GW_ROLE"       "$GW_TRUST"
ensure_role "$RT_ROLE"       "$RT_TRUST"

LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${LAMBDA_ROLE}"
BRIDGE_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${BRIDGE_ROLE}"
DATABREW_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${DATABREW_ROLE}"
GW_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${GW_ROLE}"
RT_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${RT_ROLE}"

LAMBDA_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"},
  {"Effect":"Allow","Action":["databrew:CreateDataset","databrew:UpdateDataset","databrew:DeleteDataset","databrew:DescribeDataset","databrew:CreateProfileJob","databrew:UpdateProfileJob","databrew:DescribeJob","databrew:StartJobRun","databrew:DescribeJobRun","databrew:ListJobRuns","databrew:StopJobRun"],"Resource":"*"},
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:ListBucket"],"Resource":["arn:aws:s3:::${BUCKET}","arn:aws:s3:::${BUCKET}/*"]},
  {"Effect":"Allow","Action":["iam:PassRole"],"Resource":"${DATABREW_ROLE_ARN}","Condition":{"StringEquals":{"iam:PassedToService":"databrew.amazonaws.com"}}}
]}
JSON
)
BRIDGE_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"},
  {"Effect":"Allow","Action":["secretsmanager:GetSecretValue"],"Resource":"arn:aws:secretsmanager:${REGION}:${ACCOUNT}:secret:${SECRET_NAME}*"},
  {"Effect":"Allow","Action":["cognito-idp:InitiateAuth"],"Resource":"*"},
  {"Effect":"Allow","Action":["bedrock-agentcore:InvokeAgentRuntime"],"Resource":"*"}
]}
JSON
)
DATABREW_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:ListBucket","s3:DeleteObject"],"Resource":["arn:aws:s3:::${BUCKET}","arn:aws:s3:::${BUCKET}/*"]},
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"}
]}
JSON
)
GW_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["lambda:InvokeFunction"],"Resource":"arn:aws:lambda:${REGION}:${ACCOUNT}:function:${LAMBDA_NAME}"},
  {"Effect":"Allow","Action":["bedrock-agentcore:GetWorkloadAccessToken","bedrock-agentcore:GetWorkloadAccessTokenForJWT","bedrock-agentcore:GetWorkloadAccessTokenForUserId"],"Resource":"*"},
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"}
]}
JSON
)
RT_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream","bedrock:Converse","bedrock:ConverseStream"],"Resource":[
    "arn:aws:bedrock:*::foundation-model/anthropic.*",
    "arn:aws:bedrock:*:${ACCOUNT}:inference-profile/*"
  ]},
  {"Effect":"Allow","Action":["secretsmanager:GetSecretValue"],"Resource":"arn:aws:secretsmanager:${REGION}:${ACCOUNT}:secret:${SECRET_NAME}*"},
  {"Effect":"Allow","Action":["cognito-idp:InitiateAuth"],"Resource":"*"},
  {"Effect":"Allow","Action":["s3:PutObject"],"Resource":"arn:aws:s3:::${BUCKET}/*"},
  {"Effect":"Allow","Action":["bedrock-agentcore:InvokeAgentRuntime","bedrock-agentcore:GetWorkloadAccessToken","bedrock-agentcore:GetWorkloadAccessTokenForJWT","bedrock-agentcore:GetWorkloadAccessTokenForUserId"],"Resource":"*"},
  {"Effect":"Allow","Action":["ecr:GetAuthorizationToken","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:BatchCheckLayerAvailability"],"Resource":"*"},
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogStreams","logs:DescribeLogGroups"],"Resource":"*"},
  {"Effect":"Allow","Action":["xray:PutTraceSegments","xray:PutTelemetryRecords","xray:GetSamplingRules","xray:GetSamplingTargets"],"Resource":"*"},
  {"Effect":"Allow","Action":["cloudwatch:PutMetricData"],"Resource":"*","Condition":{"StringEquals":{"cloudwatch:namespace":"bedrock-agentcore"}}}
]}
JSON
)

aws iam put-role-policy --role-name "$LAMBDA_ROLE"   --policy-name "${PREFIX}-lambda-inline"   --policy-document "$LAMBDA_POLICY"   >/dev/null
aws iam put-role-policy --role-name "$BRIDGE_ROLE"   --policy-name "${PREFIX}-bridge-inline"   --policy-document "$BRIDGE_POLICY"   >/dev/null
aws iam put-role-policy --role-name "$DATABREW_ROLE" --policy-name "${PREFIX}-databrew-inline" --policy-document "$DATABREW_POLICY" >/dev/null
aws iam put-role-policy --role-name "$GW_ROLE"       --policy-name "${PREFIX}-gw-inline"       --policy-document "$GW_POLICY"       >/dev/null
aws iam put-role-policy --role-name "$RT_ROLE"       --policy-name "${PREFIX}-rt-inline"       --policy-document "$RT_POLICY"       >/dev/null

# ─── 3. Cognito user pool, resource server, app client, user ───────────────
USER_POOL_ID="$(aws cognito-idp list-user-pools --max-results 50 --region "$REGION" --query "UserPools[?Name=='${USER_POOL_NAME}'].Id | [0]" --output text 2>/dev/null || true)"
if [ -z "$USER_POOL_ID" ] || [ "$USER_POOL_ID" = "None" ]; then
  echo "  create user pool $USER_POOL_NAME"
  USER_POOL_ID="$(aws cognito-idp create-user-pool --pool-name "$USER_POOL_NAME" --region "$REGION" \
    --policies '{"PasswordPolicy":{"MinimumLength":8,"RequireUppercase":true,"RequireLowercase":true,"RequireNumbers":true,"RequireSymbols":true}}' \
    --admin-create-user-config AllowAdminCreateUserOnly=true \
    --query 'UserPool.Id' --output text)"
fi
DOMAIN_PREFIX="${PREFIX}-${ACCOUNT}"
aws cognito-idp create-user-pool-domain --user-pool-id "$USER_POOL_ID" --domain "$DOMAIN_PREFIX" --region "$REGION" >/dev/null 2>&1 || true

if ! aws cognito-idp describe-resource-server --user-pool-id "$USER_POOL_ID" --identifier "$RESOURCE_SERVER_ID" --region "$REGION" >/dev/null 2>&1; then
  echo "  create resource server $RESOURCE_SERVER_ID"
  aws cognito-idp create-resource-server --user-pool-id "$USER_POOL_ID" --identifier "$RESOURCE_SERVER_ID" \
    --name "$RESOURCE_SERVER_ID" --scopes 'ScopeName=invoke,ScopeDescription=invoke gateway' --region "$REGION" >/dev/null
fi

CLIENT_ID="$(aws cognito-idp list-user-pool-clients --user-pool-id "$USER_POOL_ID" --region "$REGION" --query "UserPoolClients[?ClientName=='${APP_CLIENT_NAME}'].ClientId | [0]" --output text 2>/dev/null || true)"
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
# Always sync the password so re-runs (which mint a new RUNTIME_PASSWORD) don't drift
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

# ─── 4. Tools Lambda (Gateway target) ───────────────────────────────────────
echo "  package tools lambda"
cp "$HERE/tools/lambda_function.py" "$BUILD/lambda_function.py"
(cd "$BUILD" && zip -q lambda.zip lambda_function.py)

LAMBDA_ENV=$(jq -nc \
  --arg prefix "$PREFIX" --arg bucket "$BUCKET" --arg pp "$PROFILE_S3_PREFIX" --arg drole "$DATABREW_ROLE_ARN" \
  '{Variables:{PREFIX:$prefix, BUCKET:$bucket, PROFILE_PREFIX:$pp, DATABREW_ROLE_ARN:$drole}}')

if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  update tools lambda code"
  aws lambda update-function-code --function-name "$LAMBDA_NAME" --region "$REGION" --zip-file "fileb://$BUILD/lambda.zip" >/dev/null
  aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$REGION"
  aws lambda update-function-configuration --function-name "$LAMBDA_NAME" --region "$REGION" \
    --environment "$LAMBDA_ENV" --timeout 900 --memory-size 512 >/dev/null
  aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$REGION"
else
  echo "  create tools lambda"
  for i in 1 2 3 4 5 6 7 8; do
    if aws lambda create-function --function-name "$LAMBDA_NAME" --region "$REGION" \
        --runtime python3.12 --role "$LAMBDA_ROLE_ARN" \
        --handler lambda_function.lambda_handler \
        --zip-file "fileb://$BUILD/lambda.zip" \
        --timeout 900 --memory-size 512 \
        --environment "$LAMBDA_ENV" >/dev/null 2>/tmp/lc.err; then
      break
    fi
    if grep -q "cannot be assumed" /tmp/lc.err; then
      echo "    waiting for IAM... ($i)"
      sleep 5
      continue
    fi
    cat /tmp/lc.err >&2
    exit 1
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
  {"name":"profile_dataset","description":"Run an AWS Glue DataBrew FULL_DATASET profile job for the given S3 object and return the report's S3 URI.","inputSchema":{"type":"object","properties":{"s3_uri":{"type":"string","description":"S3 URI of the dataset to profile, e.g. s3://bucket/path/file.csv"},"timeout_seconds":{"type":"integer","description":"Maximum seconds to wait for job completion (default 720)."}},"required":["s3_uri"]}},
  {"name":"analyze_profiling_report","description":"Fetch a DataBrew profile report JSON from S3 and return a slim, model-friendly view with per-column statistics (histograms removed).","inputSchema":{"type":"object","properties":{"report_uri":{"type":"string","description":"S3 URI of the profile JSON, returned by profile_dataset."}},"required":["report_uri"]}}
]
JSON
)

EXISTING_TARGET="$(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --region "$REGION" --query "items[?name=='${GW_TARGET_NAME}'].targetId | [0]" --output text 2>/dev/null || true)"
if [ -n "$EXISTING_TARGET" ] && [ "$EXISTING_TARGET" != "None" ]; then
  echo "  update gateway target $GW_TARGET_NAME"
  UPDATE_PAYLOAD=$(jq -nc --arg gw "$GW_ID" --arg tid "$EXISTING_TARGET" --arg name "$GW_TARGET_NAME" --arg larn "$LAMBDA_ARN" --argjson tools "$TOOL_SCHEMA" \
    '{gatewayIdentifier:$gw, targetId:$tid, name:$name, description:"data-profiling-agent tools",
      targetConfiguration:{mcp:{lambda:{lambdaArn:$larn, toolSchema:{inlinePayload:$tools}}}},
      credentialProviderConfigurations:[{credentialProviderType:"GATEWAY_IAM_ROLE"}]}')
  aws bedrock-agentcore-control update-gateway-target --region "$REGION" --cli-input-json "$UPDATE_PAYLOAD" >/dev/null
else
  echo "  create gateway target $GW_TARGET_NAME"
  TARGET_PAYLOAD=$(jq -nc --arg gw "$GW_ID" --arg name "$GW_TARGET_NAME" --arg larn "$LAMBDA_ARN" --argjson tools "$TOOL_SCHEMA" \
    '{gatewayIdentifier:$gw, name:$name, description:"data-profiling-agent tools",
      targetConfiguration:{mcp:{lambda:{lambdaArn:$larn, toolSchema:{inlinePayload:$tools}}}},
      credentialProviderConfigurations:[{credentialProviderType:"GATEWAY_IAM_ROLE"}]}')
  aws bedrock-agentcore-control create-gateway-target --region "$REGION" --cli-input-json "$TARGET_PAYLOAD" >/dev/null
fi

aws lambda add-permission --function-name "$LAMBDA_NAME" --region "$REGION" \
  --statement-id agentcore-gw-invoke --action lambda:InvokeFunction \
  --principal bedrock-agentcore.amazonaws.com >/dev/null 2>&1 || true

# ─── 6. ECR repo + Docker build (linux/arm64) ───────────────────────────────
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
  echo "ERROR: need docker or finch installed" >&2
  exit 1
fi
echo "  container runtime: $RUNTIME, push -> $ECR_URI"
aws ecr get-login-password --region "$REGION" | "$RUNTIME" login --username AWS --password-stdin "$ECR_HOST" >/dev/null

if [ "$RUNTIME" = "docker" ]; then
  docker buildx inspect data-profiling-agent-builder >/dev/null 2>&1 || docker buildx create --name data-profiling-agent-builder --use >/dev/null
  docker buildx use data-profiling-agent-builder >/dev/null
  docker buildx build --platform linux/arm64 -t "${ECR_URI}:latest" --push "$HERE/agent" >&2
else
  finch build --platform linux/arm64 -t "${ECR_URI}:latest" "$HERE/agent" >&2
  finch push "${ECR_URI}:latest" >&2
fi

# ─── 7. AgentCore Runtime ───────────────────────────────────────────────────
RT_ENV_VARS=$(jq -nc \
  --arg gw "$GW_URL" --arg secret "$SECRET_NAME" --arg target "$GW_TARGET_NAME" \
  --arg model "$MODEL_ID" --arg region "$REGION" --arg bucket "$BUCKET" --arg ip "$INSIGHTS_PREFIX" \
  '{GATEWAY_URL:$gw, GATEWAY_CREDS_SECRET:$secret, GATEWAY_TARGET_NAME:$target,
    MODEL_ID:$model, AWS_REGION:$region, INSIGHTS_BUCKET:$bucket, INSIGHTS_PREFIX:$ip}')
RT_ID="$(aws bedrock-agentcore-control list-agent-runtimes --no-paginate --region "$REGION" --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" --output text 2>/dev/null || true)"
RT_PAYLOAD=$(jq -nc \
  --arg name "$RUNTIME_NAME" --arg image "${ECR_URI}:latest" --arg role "$RT_ROLE_ARN" \
  --arg discovery "$DISCOVERY_URL" --arg cid "$CLIENT_ID" --argjson env "$RT_ENV_VARS" \
  '{agentRuntimeName:$name,
    agentRuntimeArtifact:{containerConfiguration:{containerUri:$image}},
    networkConfiguration:{networkMode:"PUBLIC"},
    roleArn:$role,
    authorizerConfiguration:{customJWTAuthorizer:{discoveryUrl:$discovery, allowedClients:[$cid]}},
    environmentVariables:$env}')
if [ -z "$RT_ID" ] || [ "$RT_ID" = "None" ]; then
  echo "  create agent runtime $RUNTIME_NAME"
  RT_ID="$(aws bedrock-agentcore-control create-agent-runtime --region "$REGION" --cli-input-json "$RT_PAYLOAD" --query 'agentRuntimeId' --output text)"
else
  echo "  update agent runtime $RUNTIME_NAME"
  UPDATE_RT=$(jq -nc \
    --arg id "$RT_ID" --arg image "${ECR_URI}:latest" --arg role "$RT_ROLE_ARN" \
    --arg discovery "$DISCOVERY_URL" --arg cid "$CLIENT_ID" --argjson env "$RT_ENV_VARS" \
    '{agentRuntimeId:$id,
      agentRuntimeArtifact:{containerConfiguration:{containerUri:$image}},
      networkConfiguration:{networkMode:"PUBLIC"},
      roleArn:$role,
      authorizerConfiguration:{customJWTAuthorizer:{discoveryUrl:$discovery, allowedClients:[$cid]}},
      environmentVariables:$env}')
  aws bedrock-agentcore-control update-agent-runtime --region "$REGION" --cli-input-json "$UPDATE_RT" >/dev/null
fi
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  st="$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RT_ID" --region "$REGION" --query 'status' --output text)"
  echo "    runtime status: $st"
  case "$st" in READY) break;; CREATE_FAILED|UPDATE_FAILED) echo "runtime failed"; exit 1;; esac
  sleep 6
done
RT_ARN="$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RT_ID" --region "$REGION" --query 'agentRuntimeArn' --output text)"

# ─── 8. Bridge Lambda + EventBridge rule ────────────────────────────────────
echo "  package bridge lambda"
cp "$HERE/bridge/lambda_function.py" "$BUILD/bridge.py"
(cd "$BUILD" && zip -q -j bridge.zip bridge.py)

BRIDGE_ENV=$(jq -nc \
  --arg rt "$RT_ARN" --arg secret "$SECRET_NAME" --arg lp "$LANDING_PREFIX" \
  '{Variables:{RUNTIME_ARN:$rt, GATEWAY_CREDS_SECRET:$secret, LANDING_PREFIX:$lp}}')

if aws lambda get-function --function-name "$BRIDGE_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  update bridge lambda code"
  aws lambda update-function-code --function-name "$BRIDGE_NAME" --region "$REGION" --zip-file "fileb://$BUILD/bridge.zip" --publish >/dev/null
  aws lambda wait function-updated --function-name "$BRIDGE_NAME" --region "$REGION"
  aws lambda update-function-configuration --function-name "$BRIDGE_NAME" --region "$REGION" \
    --handler bridge.lambda_handler --environment "$BRIDGE_ENV" --timeout 900 --memory-size 256 >/dev/null
  aws lambda wait function-updated --function-name "$BRIDGE_NAME" --region "$REGION"
else
  echo "  create bridge lambda"
  for i in 1 2 3 4 5 6 7 8; do
    if aws lambda create-function --function-name "$BRIDGE_NAME" --region "$REGION" \
        --runtime python3.12 --role "$BRIDGE_ROLE_ARN" \
        --handler bridge.lambda_handler --zip-file "fileb://$BUILD/bridge.zip" \
        --timeout 900 --memory-size 256 --environment "$BRIDGE_ENV" >/dev/null 2>/tmp/bc.err; then
      break
    fi
    if grep -q "cannot be assumed" /tmp/bc.err; then
      echo "    waiting for IAM... ($i)"
      sleep 5
      continue
    fi
    cat /tmp/bc.err >&2
    exit 1
  done
fi
BRIDGE_ARN="$(aws lambda get-function --function-name "$BRIDGE_NAME" --region "$REGION" --query 'Configuration.FunctionArn' --output text)"

EB_PATTERN=$(jq -nc --arg bucket "$BUCKET" --arg lp "$LANDING_PREFIX" \
  '{source:["aws.s3"], "detail-type":["Object Created"],
    detail:{bucket:{name:[$bucket]}, object:{key:[{prefix:$lp}]}}}')

aws events put-rule --name "$EB_RULE_NAME" --region "$REGION" \
  --event-pattern "$EB_PATTERN" --state ENABLED >/dev/null
aws events put-targets --rule "$EB_RULE_NAME" --region "$REGION" \
  --targets "Id=1,Arn=${BRIDGE_ARN}" >/dev/null
aws lambda add-permission --function-name "$BRIDGE_NAME" --region "$REGION" \
  --statement-id eventbridge-invoke --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${REGION}:${ACCOUNT}:rule/${EB_RULE_NAME}" >/dev/null 2>&1 || true

cat <<EOF

[data-profiling-agent] deployed
  Bucket:              s3://$BUCKET
  Landing prefix:      $LANDING_PREFIX
  Profile prefix:      $PROFILE_S3_PREFIX
  Insights prefix:     $INSIGHTS_PREFIX
  Cognito user pool:   $USER_POOL_ID
  Cognito client:      $CLIENT_ID
  Test user:           $RUNTIME_USER  (creds in secret $SECRET_NAME)
  Tools Lambda:        $LAMBDA_ARN
  Gateway URL:         $GW_URL
  ECR image:           ${ECR_URI}:latest
  Runtime ARN:         $RT_ARN
  Bridge Lambda:       $BRIDGE_ARN
  EventBridge rule:    $EB_RULE_NAME

Live test:
  aws s3 cp $HERE/sample/violations.csv s3://$BUCKET/${LANDING_PREFIX}violations.csv --region $REGION
  # wait ~2-3 min, then:
  aws s3 ls s3://$BUCKET/${INSIGHTS_PREFIX} --region $REGION
  aws s3 cp s3://$BUCKET/${INSIGHTS_PREFIX}violations.json - --region $REGION

Manual invoke (bypass EventBridge):
  RUNTIME_ARN='$RT_ARN' SECRET_NAME='$SECRET_NAME' AWS_REGION='$REGION' \\
    python $HERE/scripts/invoke_agent.py s3://$BUCKET/${LANDING_PREFIX}violations.csv
EOF
