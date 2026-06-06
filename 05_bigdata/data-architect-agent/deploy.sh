#!/usr/bin/env bash
# Deploy data-architect-agent.
#
# Piggybacks on the data-profiling-agent's S3 bucket (default
# data-profiling-agent-${ACCOUNT}-${REGION}). Listens for new objects under insights/, runs
# the architect agent on AgentCore Runtime, which catalogues raw + curated
# tables in Glue and runs the curate ETL.
#
# Creates:
#   - IAM roles  data-architect-agent-lambda, -bridge, -glue, -gateway, -runtime
#   - Cognito    user pool + resource server + app client + test user
#   - Secrets    data-architect-agent/gateway-credentials
#   - Lambda     data-architect-agent-tools      (MCP tools target)
#   - Gateway    data-architect-agent-gw + Lambda target with toolSchema
#   - ECR repo   data-architect-agent + container build/push (linux/arm64)
#   - AgentCore  data_architect_agent
#   - Lambda     data-architect-agent-bridge     (insights -> runtime)
#   - EventBridge rule on s3:ObjectCreated under insights/  -> bridge
#   - Glue       data_architect_agent_curated database, curate-* jobs
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-data-architect-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

# Reuse data-profiling-agent's bucket by default.
DATA_BUCKET="${DATA_BUCKET:-data-profiling-agent-${ACCOUNT}-${REGION}}"
RAW_PREFIX="${RAW_PREFIX:-landing/}"
CURATED_PREFIX="${CURATED_PREFIX:-curated/}"
INSIGHTS_PREFIX="${INSIGHTS_PREFIX:-insights/}"
DESIGNS_PREFIX="${DESIGNS_PREFIX:-designs/}"
SCRIPTS_PREFIX="${SCRIPTS_PREFIX:-glue-scripts/}"

LAMBDA_NAME="${PREFIX}-tools"
BRIDGE_NAME="${PREFIX}-bridge"
LAMBDA_ROLE="${PREFIX}-lambda-role"
BRIDGE_ROLE="${PREFIX}-bridge-role"
GLUE_ROLE="${PREFIX}-glue-role"
GW_ROLE="${PREFIX}-gateway-role"
RT_ROLE="${PREFIX}-runtime-role"
GW_NAME="${PREFIX}-gw"
GW_TARGET_NAME="tools"
USER_POOL_NAME="${PREFIX}-pool"
RESOURCE_SERVER_ID="${PREFIX}-resource"
APP_CLIENT_NAME="${PREFIX}-client"
SECRET_NAME="${PREFIX}/gateway-credentials"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="data_architect_agent"
RUNTIME_USER="${PREFIX}-user"
RUNTIME_PASSWORD="${RUNTIME_PASSWORD:-DataArchitectAgentPass$(date +%s)!}"
EB_RULE_NAME="${PREFIX}-insights-rule"
GLUE_DATABASE="${GLUE_DATABASE:-data_architect_agent_curated}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-sonnet-4-6}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

echo "[data-architect-agent] deploy region=$REGION account=$ACCOUNT data-bucket=$DATA_BUCKET"

# ─── 1. S3 bucket sanity (must exist + have EventBridge enabled) ────────────
if ! aws s3api head-bucket --bucket "$DATA_BUCKET" --region "$REGION" >/dev/null 2>&1; then
  echo "ERROR: data bucket $DATA_BUCKET does not exist; deploy data-profiling-agent first or override DATA_BUCKET" >&2
  exit 1
fi
aws s3api put-bucket-notification-configuration --bucket "$DATA_BUCKET" --region "$REGION" \
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
GLUE_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"glue.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
GW_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
RT_TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT}"},"ArnLike":{"aws:SourceArn":"arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT}:*"}}}]}
JSON
)

ensure_role "$LAMBDA_ROLE"   "$LAMBDA_TRUST"
ensure_role "$BRIDGE_ROLE"   "$LAMBDA_TRUST"
ensure_role "$GLUE_ROLE"     "$GLUE_TRUST"
ensure_role "$GW_ROLE"       "$GW_TRUST"
ensure_role "$RT_ROLE"       "$RT_TRUST"

LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${LAMBDA_ROLE}"
BRIDGE_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${BRIDGE_ROLE}"
GLUE_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${GLUE_ROLE}"
GW_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${GW_ROLE}"
RT_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${RT_ROLE}"

LAMBDA_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"},
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:ListBucket"],"Resource":["arn:aws:s3:::${DATA_BUCKET}","arn:aws:s3:::${DATA_BUCKET}/*"]},
  {"Effect":"Allow","Action":["glue:CreateDatabase","glue:GetDatabase","glue:GetDatabases","glue:CreateTable","glue:UpdateTable","glue:GetTable","glue:GetTables","glue:DeleteTable","glue:CreateJob","glue:UpdateJob","glue:GetJob","glue:GetJobs","glue:StartJobRun","glue:GetJobRun","glue:GetJobRuns","glue:BatchStopJobRun"],"Resource":"*"},
  {"Effect":"Allow","Action":["iam:PassRole"],"Resource":"${GLUE_ROLE_ARN}","Condition":{"StringEquals":{"iam:PassedToService":"glue.amazonaws.com"}}}
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
GLUE_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:ListBucket"],"Resource":["arn:aws:s3:::${DATA_BUCKET}","arn:aws:s3:::${DATA_BUCKET}/*"]},
  {"Effect":"Allow","Action":["glue:GetTable","glue:GetTables","glue:GetDatabase","glue:GetDatabases","glue:GetPartition","glue:GetPartitions","glue:BatchCreatePartition","glue:BatchUpdatePartition","glue:BatchDeletePartition","glue:CreatePartition","glue:UpdatePartition","glue:UpdateTable"],"Resource":"*"},
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:AssociateKmsKey"],"Resource":"*"}
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
  {"Effect":"Allow","Action":["s3:PutObject","s3:GetObject"],"Resource":"arn:aws:s3:::${DATA_BUCKET}/*"},
  {"Effect":"Allow","Action":["bedrock-agentcore:InvokeAgentRuntime","bedrock-agentcore:GetWorkloadAccessToken","bedrock-agentcore:GetWorkloadAccessTokenForJWT","bedrock-agentcore:GetWorkloadAccessTokenForUserId"],"Resource":"*"},
  {"Effect":"Allow","Action":["ecr:GetAuthorizationToken","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:BatchCheckLayerAvailability"],"Resource":"*"},
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogStreams","logs:DescribeLogGroups"],"Resource":"*"},
  {"Effect":"Allow","Action":["xray:PutTraceSegments","xray:PutTelemetryRecords","xray:GetSamplingRules","xray:GetSamplingTargets"],"Resource":"*"},
  {"Effect":"Allow","Action":["cloudwatch:PutMetricData"],"Resource":"*","Condition":{"StringEquals":{"cloudwatch:namespace":"bedrock-agentcore"}}}
]}
JSON
)

aws iam put-role-policy --role-name "$LAMBDA_ROLE" --policy-name "${PREFIX}-lambda-inline" --policy-document "$LAMBDA_POLICY" >/dev/null
aws iam put-role-policy --role-name "$BRIDGE_ROLE" --policy-name "${PREFIX}-bridge-inline" --policy-document "$BRIDGE_POLICY" >/dev/null
aws iam put-role-policy --role-name "$GLUE_ROLE"   --policy-name "${PREFIX}-glue-inline"   --policy-document "$GLUE_POLICY"   >/dev/null
aws iam put-role-policy --role-name "$GW_ROLE"     --policy-name "${PREFIX}-gw-inline"     --policy-document "$GW_POLICY"     >/dev/null
aws iam put-role-policy --role-name "$RT_ROLE"     --policy-name "${PREFIX}-rt-inline"     --policy-document "$RT_POLICY"     >/dev/null

aws iam attach-role-policy --role-name "$GLUE_ROLE" \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole >/dev/null 2>&1 || true

# Pre-create the Glue database and grant Lake Formation permissions to the
# Glue role on the database + its tables. Without this, accounts that have
# Lake Formation enabled (or are using LF default permissions) will block
# the curate job's catalog write with AccessDeniedException.
if ! aws glue get-database --name "$GLUE_DATABASE" --region "$REGION" >/dev/null 2>&1; then
  echo "  create glue database $GLUE_DATABASE"
  aws glue create-database --region "$REGION" \
    --database-input "{\"Name\":\"$GLUE_DATABASE\"}" >/dev/null
fi
echo "  grant Lake Formation permissions on $GLUE_DATABASE to $GLUE_ROLE"
aws lakeformation grant-permissions --region "$REGION" \
  --principal "DataLakePrincipalIdentifier=$GLUE_ROLE_ARN" \
  --resource "{\"Database\":{\"Name\":\"$GLUE_DATABASE\"}}" \
  --permissions ALL --permissions-with-grant-option ALL >/dev/null 2>&1 || true
aws lakeformation grant-permissions --region "$REGION" \
  --principal "DataLakePrincipalIdentifier=$GLUE_ROLE_ARN" \
  --resource "{\"Table\":{\"DatabaseName\":\"$GLUE_DATABASE\",\"TableWildcard\":{}}}" \
  --permissions ALL --permissions-with-grant-option ALL >/dev/null 2>&1 || true

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
  --arg prefix "$PREFIX" --arg db "$GLUE_DATABASE" \
  '{Variables:{PREFIX:$prefix, DEFAULT_DATABASE:$db}}')

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
  {"name":"read_insights","description":"Fetch the data-profiling-agent insights JSON from S3 and return its parsed content.","inputSchema":{"type":"object","properties":{"insights_uri":{"type":"string","description":"S3 URI of the insights JSON, e.g. s3://bucket/insights/file.json"}},"required":["insights_uri"]}},
  {"name":"create_glue_database","description":"Idempotently create a Glue Catalog database.","inputSchema":{"type":"object","properties":{"database":{"type":"string","description":"Glue database name."}},"required":["database"]}},
  {"name":"create_glue_table","description":"Create or replace a Glue Catalog table. columns and partitions are arrays of {name,type}. format is csv or parquet.","inputSchema":{"type":"object","properties":{"database":{"type":"string"},"table":{"type":"string"},"columns":{"type":"array","items":{"type":"object","properties":{"name":{"type":"string"},"type":{"type":"string"}},"required":["name","type"]}},"location":{"type":"string","description":"S3 location, must end with /"},"partitions":{"type":"array","items":{"type":"object","properties":{"name":{"type":"string"},"type":{"type":"string"}},"required":["name","type"]}},"format":{"type":"string","description":"csv or parquet"}},"required":["database","table","columns","location"]}},
  {"name":"run_glue_job","description":"Idempotently create or update a persistent Glue PySpark ETL job and run it once. Polls until terminal.","inputSchema":{"type":"object","properties":{"job_name":{"type":"string"},"role_arn":{"type":"string"},"script_uri":{"type":"string","description":"S3 URI of the PySpark script."},"args":{"type":"object","description":"Map of --KEY -> value passed as Glue job args."},"timeout_minutes":{"type":"integer"}},"required":["job_name","role_arn","script_uri","args"]}}
]
JSON
)

EXISTING_TARGET="$(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --region "$REGION" --query "items[?name=='${GW_TARGET_NAME}'].targetId | [0]" --output text 2>/dev/null || true)"
if [ -n "$EXISTING_TARGET" ] && [ "$EXISTING_TARGET" != "None" ]; then
  echo "  update gateway target $GW_TARGET_NAME"
  UPDATE_PAYLOAD=$(jq -nc --arg gw "$GW_ID" --arg tid "$EXISTING_TARGET" --arg name "$GW_TARGET_NAME" --arg larn "$LAMBDA_ARN" --argjson tools "$TOOL_SCHEMA" \
    '{gatewayIdentifier:$gw, targetId:$tid, name:$name, description:"data-architect-agent tools",
      targetConfiguration:{mcp:{lambda:{lambdaArn:$larn, toolSchema:{inlinePayload:$tools}}}},
      credentialProviderConfigurations:[{credentialProviderType:"GATEWAY_IAM_ROLE"}]}')
  aws bedrock-agentcore-control update-gateway-target --region "$REGION" --cli-input-json "$UPDATE_PAYLOAD" >/dev/null
else
  echo "  create gateway target $GW_TARGET_NAME"
  TARGET_PAYLOAD=$(jq -nc --arg gw "$GW_ID" --arg name "$GW_TARGET_NAME" --arg larn "$LAMBDA_ARN" --argjson tools "$TOOL_SCHEMA" \
    '{gatewayIdentifier:$gw, name:$name, description:"data-architect-agent tools",
      targetConfiguration:{mcp:{lambda:{lambdaArn:$larn, toolSchema:{inlinePayload:$tools}}}},
      credentialProviderConfigurations:[{credentialProviderType:"GATEWAY_IAM_ROLE"}]}')
  aws bedrock-agentcore-control create-gateway-target --region "$REGION" --cli-input-json "$TARGET_PAYLOAD" >/dev/null
fi

aws lambda add-permission --function-name "$LAMBDA_NAME" --region "$REGION" \
  --statement-id agentcore-gw-invoke --action lambda:InvokeFunction \
  --principal bedrock-agentcore.amazonaws.com >/dev/null 2>&1 || true

# ─── 6. Upload Glue script ──────────────────────────────────────────────────
SCRIPT_KEY="${SCRIPTS_PREFIX}curate_job.py"
SCRIPT_URI="s3://${DATA_BUCKET}/${SCRIPT_KEY}"
echo "  upload glue script -> $SCRIPT_URI"
aws s3 cp "$HERE/glue/curate_job.py" "$SCRIPT_URI" --region "$REGION" >/dev/null

# ─── 7. ECR repo + container build (linux/arm64) ────────────────────────────
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
  docker buildx inspect data-architect-agent-builder >/dev/null 2>&1 || docker buildx create --name data-architect-agent-builder --use >/dev/null
  docker buildx use data-architect-agent-builder >/dev/null
  docker buildx build --platform linux/arm64 -t "${ECR_URI}:latest" --push "$HERE/agent" >&2
else
  finch build --platform linux/arm64 -t "${ECR_URI}:latest" "$HERE/agent" >&2
  finch push "${ECR_URI}:latest" >&2
fi

# ─── 8. AgentCore Runtime ───────────────────────────────────────────────────
RT_ENV_VARS=$(jq -nc \
  --arg gw "$GW_URL" --arg secret "$SECRET_NAME" --arg target "$GW_TARGET_NAME" \
  --arg model "$MODEL_ID" --arg region "$REGION" \
  --arg bucket "$DATA_BUCKET" --arg rp "$RAW_PREFIX" --arg cp "$CURATED_PREFIX" --arg dp "$DESIGNS_PREFIX" \
  --arg script "$SCRIPT_URI" --arg grole "$GLUE_ROLE_ARN" --arg db "$GLUE_DATABASE" --arg prefix "$PREFIX" \
  '{GATEWAY_URL:$gw, GATEWAY_CREDS_SECRET:$secret, GATEWAY_TARGET_NAME:$target,
    MODEL_ID:$model, AWS_REGION:$region,
    DATA_BUCKET:$bucket, RAW_PREFIX:$rp, CURATED_PREFIX:$cp, DESIGNS_PREFIX:$dp,
    GLUE_SCRIPT_URI:$script, GLUE_ROLE_ARN:$grole, GLUE_DATABASE:$db, PREFIX:$prefix}')
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

# ─── 9. Bridge Lambda + EventBridge rule ────────────────────────────────────
echo "  package bridge lambda"
cp "$HERE/bridge/lambda_function.py" "$BUILD/bridge.py"
(cd "$BUILD" && zip -q -j bridge.zip bridge.py)

BRIDGE_ENV=$(jq -nc \
  --arg rt "$RT_ARN" --arg secret "$SECRET_NAME" --arg ip "$INSIGHTS_PREFIX" \
  '{Variables:{RUNTIME_ARN:$rt, GATEWAY_CREDS_SECRET:$secret, INSIGHTS_PREFIX:$ip}}')

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
      echo "    waiting for IAM... ($i)"; sleep 5; continue
    fi
    cat /tmp/bc.err >&2; exit 1
  done
fi
BRIDGE_ARN="$(aws lambda get-function --function-name "$BRIDGE_NAME" --region "$REGION" --query 'Configuration.FunctionArn' --output text)"

EB_PATTERN=$(jq -nc --arg bucket "$DATA_BUCKET" --arg ip "$INSIGHTS_PREFIX" \
  '{source:["aws.s3"], "detail-type":["Object Created"],
    detail:{bucket:{name:[$bucket]}, object:{key:[{prefix:$ip}]}}}')

aws events put-rule --name "$EB_RULE_NAME" --region "$REGION" \
  --event-pattern "$EB_PATTERN" --state ENABLED >/dev/null
aws events put-targets --rule "$EB_RULE_NAME" --region "$REGION" \
  --targets "Id=1,Arn=${BRIDGE_ARN}" >/dev/null
aws lambda add-permission --function-name "$BRIDGE_NAME" --region "$REGION" \
  --statement-id eventbridge-invoke --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${REGION}:${ACCOUNT}:rule/${EB_RULE_NAME}" >/dev/null 2>&1 || true

cat <<EOF

[data-architect-agent] deployed
  Data bucket:         s3://$DATA_BUCKET
  Insights prefix:     $INSIGHTS_PREFIX  (input trigger)
  Curated prefix:      $CURATED_PREFIX
  Designs prefix:      $DESIGNS_PREFIX
  Glue script:         $SCRIPT_URI
  Glue role:           $GLUE_ROLE_ARN
  Glue database:       $GLUE_DATABASE
  Cognito user pool:   $USER_POOL_ID
  Cognito client:      $CLIENT_ID
  Test user:           $RUNTIME_USER  (creds in secret $SECRET_NAME)
  Tools Lambda:        $LAMBDA_ARN
  Gateway URL:         $GW_URL
  ECR image:           ${ECR_URI}:latest
  Runtime ARN:         $RT_ARN
  Bridge Lambda:       $BRIDGE_ARN
  EventBridge rule:    $EB_RULE_NAME

Live test (after data-profiling-agent has produced an insights file):
  aws s3 ls s3://$DATA_BUCKET/${INSIGHTS_PREFIX} --region $REGION
  # re-trigger profiling (or copy a new dataset to landing/) to fire the chain
  aws s3 cp $HERE/../data-profiling-agent/sample/violations.csv s3://$DATA_BUCKET/${RAW_PREFIX}violations.csv --region $REGION
  # wait ~3-6 minutes (profile job + databrew + glue), then:
  aws glue get-tables --database-name $GLUE_DATABASE --region $REGION --query 'TableList[].Name'
  aws s3 ls s3://$DATA_BUCKET/${CURATED_PREFIX} --region $REGION

Manual invoke (bypass EventBridge):
  RUNTIME_ARN='$RT_ARN' SECRET_NAME='$SECRET_NAME' AWS_REGION='$REGION' \\
    python $HERE/scripts/invoke_agent.py s3://$DATA_BUCKET/${INSIGHTS_PREFIX}violations.json
EOF
