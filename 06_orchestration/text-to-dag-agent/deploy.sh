#!/usr/bin/env bash
# Deploy text-to-dag-agent.
#
# Creates:
#   - Cognito user pool + client + test user (Gateway auth)
#   - IAM roles: text-to-dag-agent-lambda, text-to-dag-agent-sfn-exec, text-to-dag-agent-gateway, text-to-dag-agent-runtime
#   - Lambda  text-to-dag-agent-tools  (5 MCP tools behind one function)
#   - AgentCore Gateway  text-to-dag-agent-gw  + Lambda target with toolSchema
#   - ECR repo   text-to-dag-agent  + Docker build/push (linux/arm64)
#   - AgentCore Runtime  text_to_dag_agent  pointing at the ECR image
#   - Secrets Manager secret with Gateway client creds + agent runtime ARN
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-text-to-dag-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

LAMBDA_NAME="${PREFIX}-tools"
LAMBDA_ROLE="${PREFIX}-lambda-role"
SFN_ROLE="${PREFIX}-sfn-role"
GW_ROLE="${PREFIX}-gateway-role"
RT_ROLE="${PREFIX}-runtime-role"
GW_NAME="${PREFIX}-gw"
GW_TARGET_NAME="tools"
USER_POOL_NAME="${PREFIX}-pool"
RESOURCE_SERVER_ID="${PREFIX}-resource"
APP_CLIENT_NAME="${PREFIX}-client"
SECRET_NAME="${PREFIX}/gateway-credentials"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="text_to_dag_agent"
RUNTIME_USER="${PREFIX}-user"
RUNTIME_PASSWORD="${RUNTIME_PASSWORD:-TextToDagPass$(date +%s)!}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-haiku-4-5-20251001-v1:0}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

echo "[text-to-dag-agent] deploy region=$REGION account=$ACCOUNT"

# ─── 1. IAM roles ────────────────────────────────────────────────────────────
ensure_role() {
  local name="$1" trust="$2"
  if ! aws iam get-role --role-name "$name" >/dev/null 2>&1; then
    echo "  create role $name"
    aws iam create-role --role-name "$name" --assume-role-policy-document "$trust" >/dev/null
  fi
}

LAMBDA_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
SFN_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"states.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
GW_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
RT_TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT}"},"ArnLike":{"aws:SourceArn":"arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT}:*"}}}]}
JSON
)

ensure_role "$LAMBDA_ROLE" "$LAMBDA_TRUST"
ensure_role "$SFN_ROLE"    "$SFN_TRUST"
ensure_role "$GW_ROLE"     "$GW_TRUST"
ensure_role "$RT_ROLE"     "$RT_TRUST"

LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${LAMBDA_ROLE}"
SFN_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${SFN_ROLE}"
GW_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${GW_ROLE}"
RT_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${RT_ROLE}"

LAMBDA_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"},
  {"Effect":"Allow","Action":["bedrock:InvokeModel"],"Resource":[
    "arn:aws:bedrock:*::foundation-model/anthropic.*",
    "arn:aws:bedrock:*:${ACCOUNT}:inference-profile/*"
  ]},
  {"Effect":"Allow","Action":["states:CreateStateMachine","states:UpdateStateMachine","states:DescribeStateMachine"],"Resource":"arn:aws:states:${REGION}:${ACCOUNT}:stateMachine:${PREFIX}*"},
  {"Effect":"Allow","Action":["states:ValidateStateMachineDefinition"],"Resource":"*"},
  {"Effect":"Allow","Action":["iam:PassRole"],"Resource":"${SFN_ROLE_ARN}","Condition":{"StringEquals":{"iam:PassedToService":"states.amazonaws.com"}}},
  {"Effect":"Allow","Action":["sts:GetCallerIdentity"],"Resource":"*"}
]}
JSON
)
SFN_POLICY='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["logs:*"],"Resource":"*"}]}'
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
  {"Effect":"Allow","Action":["bedrock-agentcore:InvokeAgentRuntime","bedrock-agentcore:GetWorkloadAccessToken","bedrock-agentcore:GetWorkloadAccessTokenForJWT","bedrock-agentcore:GetWorkloadAccessTokenForUserId"],"Resource":"*"},
  {"Effect":"Allow","Action":["ecr:GetAuthorizationToken","ecr:BatchGetImage","ecr:GetDownloadUrlForLayer","ecr:BatchCheckLayerAvailability"],"Resource":"*"},
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents","logs:DescribeLogStreams","logs:DescribeLogGroups"],"Resource":"*"},
  {"Effect":"Allow","Action":["xray:PutTraceSegments","xray:PutTelemetryRecords","xray:GetSamplingRules","xray:GetSamplingTargets"],"Resource":"*"},
  {"Effect":"Allow","Action":["cloudwatch:PutMetricData"],"Resource":"*","Condition":{"StringEquals":{"cloudwatch:namespace":"bedrock-agentcore"}}}
]}
JSON
)

aws iam put-role-policy --role-name "$LAMBDA_ROLE" --policy-name "${PREFIX}-lambda-inline" --policy-document "$LAMBDA_POLICY" >/dev/null
aws iam put-role-policy --role-name "$SFN_ROLE"    --policy-name "${PREFIX}-sfn-inline"    --policy-document "$SFN_POLICY"    >/dev/null
aws iam put-role-policy --role-name "$GW_ROLE"     --policy-name "${PREFIX}-gw-inline"     --policy-document "$GW_POLICY"     >/dev/null
aws iam put-role-policy --role-name "$RT_ROLE"     --policy-name "${PREFIX}-rt-inline"     --policy-document "$RT_POLICY"     >/dev/null

# ─── 2. Cognito user pool, resource server, app client, user ────────────────
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

# Resource server
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

# Test user
if ! aws cognito-idp admin-get-user --user-pool-id "$USER_POOL_ID" --username "$RUNTIME_USER" --region "$REGION" >/dev/null 2>&1; then
  echo "  create test user $RUNTIME_USER"
  aws cognito-idp admin-create-user --user-pool-id "$USER_POOL_ID" --username "$RUNTIME_USER" \
    --temporary-password "$RUNTIME_PASSWORD" --message-action SUPPRESS --region "$REGION" >/dev/null
  aws cognito-idp admin-set-user-password --user-pool-id "$USER_POOL_ID" --username "$RUNTIME_USER" \
    --password "$RUNTIME_PASSWORD" --permanent --region "$REGION" >/dev/null
fi

DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/${USER_POOL_ID}/.well-known/openid-configuration"

# Save creds to Secrets Manager
SECRET_PAYLOAD=$(jq -nc --arg cid "$CLIENT_ID" --arg user "$RUNTIME_USER" --arg pw "$RUNTIME_PASSWORD" --arg pool "$USER_POOL_ID" \
  '{client_id:$cid, username:$user, password:$pw, user_pool_id:$pool}')
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws secretsmanager update-secret --secret-id "$SECRET_NAME" --secret-string "$SECRET_PAYLOAD" --region "$REGION" >/dev/null
else
  aws secretsmanager create-secret --name "$SECRET_NAME" --secret-string "$SECRET_PAYLOAD" --region "$REGION" >/dev/null
fi

# ─── 3. Lambda (tool target) ────────────────────────────────────────────────
echo "  package lambda"
cp "$HERE/tools/lambda_function.py" "$BUILD/lambda_function.py"
(cd "$BUILD" && zip -q lambda.zip lambda_function.py)

LAMBDA_ENV=$(cat <<JSON
{"Variables":{"BEDROCK_MODEL_ID":"${MODEL_ID}","SFN_ROLE_ARN":"${SFN_ROLE_ARN}","STATE_MACHINE_NAME_PREFIX":"${PREFIX}","ALLOWED_TASK_RESOURCE_PREFIXES":"arn:aws:states:::glue:,arn:aws:states:::lambda:,arn:aws:states:::aws-sdk:"}}
JSON
)

if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  update lambda code"
  aws lambda update-function-code --function-name "$LAMBDA_NAME" --region "$REGION" --zip-file "fileb://$BUILD/lambda.zip" >/dev/null
  aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$REGION"
  aws lambda update-function-configuration --function-name "$LAMBDA_NAME" --region "$REGION" \
    --environment "$LAMBDA_ENV" --timeout 90 --memory-size 512 >/dev/null
  aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$REGION"
else
  echo "  create lambda"
  for i in 1 2 3 4 5 6 7 8; do
    if aws lambda create-function --function-name "$LAMBDA_NAME" --region "$REGION" \
        --runtime python3.12 --role "$LAMBDA_ROLE_ARN" \
        --handler lambda_function.lambda_handler \
        --zip-file "fileb://$BUILD/lambda.zip" \
        --timeout 90 --memory-size 512 \
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

# ─── 4. AgentCore Gateway + Lambda target ───────────────────────────────────
GW_ID="$(aws bedrock-agentcore-control list-gateways --region "$REGION" --query "items[?name=='${GW_NAME}'].gatewayId | [0]" --output text 2>/dev/null || true)"
if [ -z "$GW_ID" ] || [ "$GW_ID" = "None" ]; then
  echo "  create gateway $GW_NAME"
  GW_PAYLOAD=$(jq -nc --arg name "$GW_NAME" --arg role "$GW_ROLE_ARN" --arg discovery "$DISCOVERY_URL" --arg cid "$CLIENT_ID" \
    '{name:$name, roleArn:$role, protocolType:"MCP", authorizerType:"CUSTOM_JWT",
      authorizerConfiguration:{customJWTAuthorizer:{discoveryUrl:$discovery, allowedClients:[$cid]}}}')
  GW_ID="$(aws bedrock-agentcore-control create-gateway --region "$REGION" --cli-input-json "$GW_PAYLOAD" --query 'gatewayId' --output text)"
  # Wait for READY
  for i in 1 2 3 4 5 6 7 8 9 10; do
    s="$(aws bedrock-agentcore-control get-gateway --gateway-identifier "$GW_ID" --region "$REGION" --query 'status' --output text)"
    [ "$s" = "READY" ] && break
    sleep 3
  done
fi
GW_URL="$(aws bedrock-agentcore-control get-gateway --gateway-identifier "$GW_ID" --region "$REGION" --query 'gatewayUrl' --output text)"

# Tool schema for the four tools
TOOL_SCHEMA=$(cat <<'JSON'
[
  {"name":"generate_asl","description":"Generate an AWS Step Functions ASL JSON definition for the described pipeline.","inputSchema":{"type":"object","properties":{"description":{"type":"string"}},"required":["description"]}},
  {"name":"generate_airflow_dag","description":"Generate an Apache Airflow 2.x DAG (Python source) for the described pipeline.","inputSchema":{"type":"object","properties":{"description":{"type":"string"}},"required":["description"]}},
  {"name":"generate_iam_policy","description":"Generate a least-privilege IAM policy for a Step Functions execution role given an ASL definition.","inputSchema":{"type":"object","properties":{"definition":{"type":"string"}},"required":["definition"]}},
  {"name":"validate_asl","description":"Validate an ASL definition: structural rules + Resource ARN allowlist + AWS ValidateStateMachineDefinition lint diagnostics.","inputSchema":{"type":"object","properties":{"definition":{"type":"string"}},"required":["definition"]}},
  {"name":"validate_airflow_dag","description":"AST-validate an Airflow DAG (Python source). Checks syntax, airflow imports, DAG(...) usage, dag_id, catchup, duplicate task_ids.","inputSchema":{"type":"object","properties":{"dag_python":{"type":"string"}},"required":["dag_python"]}},
  {"name":"deploy_sfn","description":"Deploy (create or update) a Step Functions state machine. Returns the ARN.","inputSchema":{"type":"object","properties":{"name":{"type":"string"},"definition":{"type":"string"}},"required":["name","definition"]}}
]
JSON
)

EXISTING_TARGET="$(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --region "$REGION" --query "items[?name=='${GW_TARGET_NAME}'].targetId | [0]" --output text 2>/dev/null || true)"
TARGET_PAYLOAD=$(jq -nc --arg gw "$GW_ID" --arg name "$GW_TARGET_NAME" --arg larn "$LAMBDA_ARN" --argjson tools "$TOOL_SCHEMA" \
  '{gatewayIdentifier:$gw, name:$name, description:"text-to-dag-agent tools",
    targetConfiguration:{mcp:{lambda:{lambdaArn:$larn, toolSchema:{inlinePayload:$tools}}}},
    credentialProviderConfigurations:[{credentialProviderType:"GATEWAY_IAM_ROLE"}]}')
if [ -n "$EXISTING_TARGET" ] && [ "$EXISTING_TARGET" != "None" ]; then
  echo "  update gateway target $GW_TARGET_NAME"
  UPDATE_PAYLOAD=$(jq -nc --arg gw "$GW_ID" --arg tid "$EXISTING_TARGET" --arg name "$GW_TARGET_NAME" --arg larn "$LAMBDA_ARN" --argjson tools "$TOOL_SCHEMA" \
    '{gatewayIdentifier:$gw, targetId:$tid, name:$name, description:"text-to-dag-agent tools",
      targetConfiguration:{mcp:{lambda:{lambdaArn:$larn, toolSchema:{inlinePayload:$tools}}}},
      credentialProviderConfigurations:[{credentialProviderType:"GATEWAY_IAM_ROLE"}]}')
  aws bedrock-agentcore-control update-gateway-target --region "$REGION" --cli-input-json "$UPDATE_PAYLOAD" >/dev/null
else
  echo "  create gateway target $GW_TARGET_NAME"
  aws bedrock-agentcore-control create-gateway-target --region "$REGION" --cli-input-json "$TARGET_PAYLOAD" >/dev/null
fi

# Lambda permission for Gateway to invoke
aws lambda add-permission --function-name "$LAMBDA_NAME" --region "$REGION" \
  --statement-id agentcore-gw-invoke --action lambda:InvokeFunction \
  --principal bedrock-agentcore.amazonaws.com >/dev/null 2>&1 || true

# ─── 5. ECR repo + Docker image (linux/arm64 required by AgentCore) ─────────
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
  docker buildx inspect text-to-dag-agent-builder >/dev/null 2>&1 || docker buildx create --name text-to-dag-agent-builder --use >/dev/null
  docker buildx use text-to-dag-agent-builder >/dev/null
  docker buildx build --platform linux/arm64 -t "${ECR_URI}:latest" --push "$HERE/agent" >&2
else
  finch build --platform linux/arm64 -t "${ECR_URI}:latest" "$HERE/agent" >&2
  finch push "${ECR_URI}:latest" >&2
fi

# ─── 6. AgentCore Runtime ───────────────────────────────────────────────────
RT_ENV_VARS=$(cat <<JSON
{"GATEWAY_URL":"${GW_URL}","GATEWAY_CREDS_SECRET":"${SECRET_NAME}","GATEWAY_TARGET_NAME":"${GW_TARGET_NAME}","MODEL_ID":"${MODEL_ID}","AWS_REGION":"${REGION}"}
JSON
)
RT_ID="$(aws bedrock-agentcore-control list-agent-runtimes --no-paginate --region "$REGION" --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" --output text 2>/dev/null || true)"
RT_PAYLOAD=$(jq -nc \
  --arg name "$RUNTIME_NAME" \
  --arg image "${ECR_URI}:latest" \
  --arg role "$RT_ROLE_ARN" \
  --arg discovery "$DISCOVERY_URL" \
  --arg cid "$CLIENT_ID" \
  --argjson env "$RT_ENV_VARS" \
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
    --arg id "$RT_ID" \
    --arg image "${ECR_URI}:latest" \
    --arg role "$RT_ROLE_ARN" \
    --arg discovery "$DISCOVERY_URL" \
    --arg cid "$CLIENT_ID" \
    --argjson env "$RT_ENV_VARS" \
    '{agentRuntimeId:$id,
      agentRuntimeArtifact:{containerConfiguration:{containerUri:$image}},
      networkConfiguration:{networkMode:"PUBLIC"},
      roleArn:$role,
      authorizerConfiguration:{customJWTAuthorizer:{discoveryUrl:$discovery, allowedClients:[$cid]}},
      environmentVariables:$env}')
  aws bedrock-agentcore-control update-agent-runtime --region "$REGION" --cli-input-json "$UPDATE_RT" >/dev/null
fi

# Wait for runtime READY
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  st="$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RT_ID" --region "$REGION" --query 'status' --output text)"
  echo "    runtime status: $st"
  case "$st" in READY) break;; CREATE_FAILED|UPDATE_FAILED) echo "runtime failed"; exit 1;; esac
  sleep 6
done

RT_ARN="$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id "$RT_ID" --region "$REGION" --query 'agentRuntimeArn' --output text)"

cat <<EOF

[text-to-dag-agent] deployed
  Cognito user pool:   $USER_POOL_ID
  Cognito client:      $CLIENT_ID
  Test user:           $RUNTIME_USER  (creds in secret $SECRET_NAME)
  Lambda (tools):      $LAMBDA_ARN
  Gateway:             $GW_URL
  Gateway target:      $GW_TARGET_NAME
  ECR image:           ${ECR_URI}:latest
  Runtime ARN:         $RT_ARN
  Runtime ID:          $RT_ID

Quick test (from agent/ dir):
  cd agent && python -m venv .venv && source .venv/bin/activate
  pip install -r requirements.txt
  RUNTIME_ARN='$RT_ARN' SECRET_NAME='$SECRET_NAME' AWS_REGION='$REGION' \\
    python ../scripts/invoke_agent.py "Build a Glue job orchestration that runs RawIngest, then Transform, then Publish, retrying transient failures. Deploy it as 'demo-pipeline'."
EOF
