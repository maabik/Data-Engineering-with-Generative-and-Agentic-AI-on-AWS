#!/usr/bin/env bash
# Deploy data-quality-deequ-agent.
#
# Piggybacks on the data-architect-agent's S3 bucket (default
# data-profiling-agent-${ACCOUNT}-${REGION}). Listens for new objects under designs/, runs
# the dq agent on AgentCore Runtime, which kicks off the persistent
# Deequ Glue job, registers the report table in Glue, and queries it via
# Athena.
#
# Creates:
#   - IAM roles  data-quality-deequ-agent-lambda, -bridge, -glue, -gateway, -runtime
#   - Cognito    user pool + resource server + app client + test user
#   - Secrets    data-quality-deequ-agent/gateway-credentials
#   - Lambda     data-quality-deequ-agent-tools  (MCP tools target)
#   - Gateway    data-quality-deequ-agent-gw + Lambda target with toolSchema
#   - ECR repo   data-quality-deequ-agent + container build/push (linux/arm64)
#   - AgentCore  data_quality_deequ_agent
#   - Lambda     data-quality-deequ-agent-bridge (designs -> runtime)
#   - EventBridge rule on s3:ObjectCreated under designs/  -> bridge
#   - Athena     data-quality-deequ-agent-wg workgroup + data_quality_deequ_agent_dq database
#   - Glue       persistent data-quality-deequ-agent-deequ-job (pydeequ + Deequ JAR via Maven)
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-data-quality-deequ-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

DATA_BUCKET="${DATA_BUCKET:-data-profiling-agent-${ACCOUNT}-${REGION}}"
DESIGNS_PREFIX="${DESIGNS_PREFIX:-designs/}"
REPORT_PREFIX="${REPORT_PREFIX:-dq-reports/}"
INSIGHTS_PREFIX="${INSIGHTS_PREFIX:-dq-insights/}"
SCRIPTS_PREFIX="${SCRIPTS_PREFIX:-glue-scripts/}"
ATHENA_OUTPUT_PREFIX="${ATHENA_OUTPUT_PREFIX:-athena-results/}"

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
RUNTIME_NAME="data_quality_deequ_agent"
RUNTIME_USER="${PREFIX}-user"
RUNTIME_PASSWORD="${RUNTIME_PASSWORD:-DataQualityDeequAgentPass$(date +%s)!}"
EB_RULE_NAME="${PREFIX}-designs-rule"
DEEQU_JOB_NAME="${PREFIX}-deequ-job"
DQ_DATABASE="${DQ_DATABASE:-data_quality_deequ_agent_dq}"
DQ_TABLE="${DQ_TABLE:-dq_results}"
ATHENA_WORKGROUP="${ATHENA_WORKGROUP:-${PREFIX}-wg}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-sonnet-4-6}"

# pydeequ 1.4.0 expects:
#   - Check.isComplete(col, hint, analyzerOptions) — needs Deequ >=2.0.7
#   - rule constructors with no args (e.g. RetainCompletenessRule()) — those
#     constructors only exist in Deequ 2.0.7-spark-3.3 (newer versions
#     promote them to apply() factory + private ctor).
DEEQU_JAR_COORD="${DEEQU_JAR_COORD:-com.amazon.deequ:deequ:2.0.7-spark-3.3}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

echo "[data-quality-deequ-agent] deploy region=$REGION account=$ACCOUNT data-bucket=$DATA_BUCKET"

if ! aws s3api head-bucket --bucket "$DATA_BUCKET" --region "$REGION" >/dev/null 2>&1; then
  echo "ERROR: data bucket $DATA_BUCKET does not exist; deploy data-architect-agent first or override DATA_BUCKET" >&2
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
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:ListBucket","s3:GetBucketLocation"],"Resource":["arn:aws:s3:::${DATA_BUCKET}","arn:aws:s3:::${DATA_BUCKET}/*"]},
  {"Effect":"Allow","Action":["glue:CreateDatabase","glue:GetDatabase","glue:GetDatabases","glue:CreateTable","glue:UpdateTable","glue:GetTable","glue:GetTables","glue:DeleteTable","glue:GetJob","glue:StartJobRun","glue:GetJobRun","glue:GetJobRuns","glue:BatchStopJobRun"],"Resource":"*"},
  {"Effect":"Allow","Action":["athena:StartQueryExecution","athena:GetQueryExecution","athena:GetQueryResults","athena:GetWorkGroup"],"Resource":"*"},
  {"Effect":"Allow","Action":["lakeformation:GetDataAccess"],"Resource":"*"}
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
  {"Effect":"Allow","Action":["lakeformation:GetDataAccess"],"Resource":"*"},
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

# The Glue/Spark session resolves the `default` database during catalog init,
# so the Deequ Glue role needs at minimum Describe on default — without it
# Spark fails before reading the actual curated table.
echo "  grant Lake Formation Describe on default db to $GLUE_ROLE"
aws lakeformation grant-permissions --region "$REGION" \
  --principal "DataLakePrincipalIdentifier=$GLUE_ROLE_ARN" \
  --resource '{"Database":{"Name":"default"}}' \
  --permissions DESCRIBE >/dev/null 2>&1 || true
aws lakeformation grant-permissions --region "$REGION" \
  --principal "DataLakePrincipalIdentifier=$GLUE_ROLE_ARN" \
  --resource '{"Table":{"DatabaseName":"default","TableWildcard":{}}}' \
  --permissions DESCRIBE >/dev/null 2>&1 || true

# Pre-create the DQ database and grant Lake Formation permissions on it
# and on the data_architect_agent_curated database (so the Deequ job can read curated tables).
DAA_DATABASE="${DAA_DATABASE:-data_architect_agent_curated}"
for db in "$DQ_DATABASE" "$DAA_DATABASE"; do
  if ! aws glue get-database --name "$db" --region "$REGION" >/dev/null 2>&1; then
    if [ "$db" = "$DQ_DATABASE" ]; then
      echo "  create glue database $db"
      aws glue create-database --region "$REGION" \
        --database-input "{\"Name\":\"$db\"}" >/dev/null
    else
      echo "  WARN: $db not found (data-architect-agent should have created it)"
      continue
    fi
  fi
  echo "  grant Lake Formation perms on $db to $GLUE_ROLE / $LAMBDA_ROLE"
  for principal in "$GLUE_ROLE_ARN" "$LAMBDA_ROLE_ARN"; do
    aws lakeformation grant-permissions --region "$REGION" \
      --principal "DataLakePrincipalIdentifier=$principal" \
      --resource "{\"Database\":{\"Name\":\"$db\"}}" \
      --permissions ALL --permissions-with-grant-option ALL >/dev/null 2>&1 || true
    aws lakeformation grant-permissions --region "$REGION" \
      --principal "DataLakePrincipalIdentifier=$principal" \
      --resource "{\"Table\":{\"DatabaseName\":\"$db\",\"TableWildcard\":{}}}" \
      --permissions ALL --permissions-with-grant-option ALL >/dev/null 2>&1 || true
  done
done

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

# ─── 4. Athena workgroup + DQ database ──────────────────────────────────────
ATHENA_OUTPUT_LOCATION="s3://${DATA_BUCKET}/${ATHENA_OUTPUT_PREFIX}"
WG_CONFIG=$(jq -nc --arg out "$ATHENA_OUTPUT_LOCATION" \
  '{ResultConfiguration:{OutputLocation:$out}, EnforceWorkGroupConfiguration:false, PublishCloudWatchMetricsEnabled:false}')
WG_UPDATES=$(jq -nc --arg out "$ATHENA_OUTPUT_LOCATION" \
  '{ResultConfigurationUpdates:{OutputLocation:$out}, EnforceWorkGroupConfiguration:false, PublishCloudWatchMetricsEnabled:false}')
if ! aws athena get-work-group --work-group "$ATHENA_WORKGROUP" --region "$REGION" >/dev/null 2>&1; then
  echo "  create athena workgroup $ATHENA_WORKGROUP"
  aws athena create-work-group --name "$ATHENA_WORKGROUP" --region "$REGION" \
    --configuration "$WG_CONFIG" >/dev/null
else
  aws athena update-work-group --work-group "$ATHENA_WORKGROUP" --region "$REGION" \
    --configuration-updates "$WG_UPDATES" >/dev/null
fi

# ─── 5. Upload Glue Deequ script + Athena workgroup ────────────────────────
SCRIPT_KEY="${SCRIPTS_PREFIX}deequ_job.py"
SCRIPT_URI="s3://${DATA_BUCKET}/${SCRIPT_KEY}"
echo "  upload glue script -> $SCRIPT_URI"
aws s3 cp "$HERE/glue/deequ_job.py" "$SCRIPT_URI" --region "$REGION" >/dev/null

# ─── 6. Persistent Deequ Glue job ──────────────────────────────────────────
GLUE_TEMP_DIR="s3://${DATA_BUCKET}/glue-temp/"
# Glue workers don't reach Maven Central, so we ship the Deequ JAR as
# an S3-hosted dependency via --extra-jars. The JAR is fetched from
# Maven during deploy, uploaded to s3://${DATA_BUCKET}/glue-scripts/jars/,
# and referenced explicitly here.
DEEQU_JAR_FILENAME="${DEEQU_JAR_COORD##*:}"
DEEQU_JAR_FILENAME="deequ-${DEEQU_JAR_FILENAME}.jar"
DEEQU_JAR_S3="s3://${DATA_BUCKET}/${SCRIPTS_PREFIX}jars/${DEEQU_JAR_FILENAME}"
DEEQU_JAR_LOCAL="$BUILD/${DEEQU_JAR_FILENAME}"
if ! aws s3api head-object --bucket "$DATA_BUCKET" --key "${SCRIPTS_PREFIX}jars/${DEEQU_JAR_FILENAME}" --region "$REGION" >/dev/null 2>&1; then
  DEEQU_JAR_URL="https://repo1.maven.org/maven2/com/amazon/deequ/deequ/${DEEQU_JAR_COORD##*:}/${DEEQU_JAR_FILENAME}"
  echo "  download deequ jar from $DEEQU_JAR_URL"
  curl -fsSL "$DEEQU_JAR_URL" -o "$DEEQU_JAR_LOCAL"
  aws s3 cp "$DEEQU_JAR_LOCAL" "$DEEQU_JAR_S3" --region "$REGION" >/dev/null
fi

DEEQU_DEFAULT_ARGS=$(jq -nc \
  --arg tmp "$GLUE_TEMP_DIR" \
  --arg jar "$DEEQU_JAR_S3" \
  '{
    "--TempDir":$tmp,
    "--enable-metrics":"true",
    "--enable-continuous-cloudwatch-log":"true",
    "--enable-glue-datacatalog":"true",
    "--enable-spark-ui":"false",
    "--job-bookmark-option":"job-bookmark-disable",
    "--additional-python-modules":"pydeequ==1.4.0",
    "--user-jars-first":"true",
    "--extra-jars":$jar
  }')

DEEQU_JOB_DEF=$(jq -nc \
  --arg name "$DEEQU_JOB_NAME" \
  --arg role "$GLUE_ROLE_ARN" \
  --arg script "$SCRIPT_URI" \
  --argjson args "$DEEQU_DEFAULT_ARGS" \
  '{Name:$name,
    Role:$role,
    Command:{Name:"glueetl", ScriptLocation:$script, PythonVersion:"3"},
    GlueVersion:"4.0",
    NumberOfWorkers:2,
    WorkerType:"G.1X",
    Timeout:60,
    DefaultArguments:$args}')

if aws glue get-job --job-name "$DEEQU_JOB_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  update glue job $DEEQU_JOB_NAME"
  UPDATE_DEF=$(echo "$DEEQU_JOB_DEF" | jq 'del(.Name)')
  aws glue update-job --region "$REGION" --job-name "$DEEQU_JOB_NAME" --job-update "$UPDATE_DEF" >/dev/null
else
  echo "  create glue job $DEEQU_JOB_NAME"
  aws glue create-job --region "$REGION" --cli-input-json "$DEEQU_JOB_DEF" >/dev/null
fi

# ─── 7. Tools Lambda (Gateway target) ───────────────────────────────────────
echo "  package tools lambda"
cp "$HERE/tools/lambda_function.py" "$BUILD/lambda_function.py"
(cd "$BUILD" && zip -q lambda.zip lambda_function.py)

LAMBDA_ENV=$(jq -nc \
  --arg prefix "$PREFIX" --arg bucket "$DATA_BUCKET" --arg rp "$REPORT_PREFIX" \
  --arg jn "$DEEQU_JOB_NAME" --arg db "$DQ_DATABASE" --arg tbl "$DQ_TABLE" \
  --arg wg "$ATHENA_WORKGROUP" --arg out "$ATHENA_OUTPUT_LOCATION" \
  '{Variables:{PREFIX:$prefix, REPORT_BUCKET:$bucket, REPORT_PREFIX:$rp,
                DEEQU_JOB_NAME:$jn, DQ_DATABASE:$db, DQ_TABLE:$tbl,
                ATHENA_WORKGROUP:$wg, ATHENA_OUTPUT_LOCATION:$out}}')

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

# ─── 8. AgentCore Gateway + Lambda target ───────────────────────────────────
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
  {"name":"read_design","description":"Fetch the data-architect-agent design JSON from S3 and return its parsed content.","inputSchema":{"type":"object","properties":{"design_uri":{"type":"string","description":"S3 URI of the design JSON, e.g. s3://bucket/designs/file.json"}},"required":["design_uri"]}},
  {"name":"run_deequ_job","description":"Start the persistent Deequ Glue job against database.table for run_id and poll until terminal. Returns suggestions/results URIs.","inputSchema":{"type":"object","properties":{"database":{"type":"string"},"table":{"type":"string"},"run_id":{"type":"string"},"timeout_minutes":{"type":"integer"},"min_null_fraction":{"type":"number","description":"Skip Completeness checks below this non-null fraction (default 0.99)."}},"required":["database","table","run_id"]}},
  {"name":"register_report_table","description":"Register or refresh the dq_results Glue Catalog table over the Parquet output for source_table. Uses Athena partition projection so MSCK is not needed.","inputSchema":{"type":"object","properties":{"source_table":{"type":"string","description":"The curated table whose DQ results we are registering, e.g. violations_curated"},"database":{"type":"string"},"table":{"type":"string"}},"required":["source_table"]}},
  {"name":"query_report","description":"Run an Athena query against the dq workgroup and return rows.","inputSchema":{"type":"object","properties":{"query":{"type":"string"},"output_location":{"type":"string"}},"required":["query"]}},
  {"name":"derive_quality_insights","description":"Read a Deequ run's results JSON and return a slim summary (passed, failed, top failing checks).","inputSchema":{"type":"object","properties":{"run_id":{"type":"string"},"database":{"type":"string"},"table":{"type":"string"}},"required":["run_id","database","table"]}}
]
JSON
)

EXISTING_TARGET="$(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --region "$REGION" --query "items[?name=='${GW_TARGET_NAME}'].targetId | [0]" --output text 2>/dev/null || true)"
if [ -n "$EXISTING_TARGET" ] && [ "$EXISTING_TARGET" != "None" ]; then
  echo "  update gateway target $GW_TARGET_NAME"
  UPDATE_PAYLOAD=$(jq -nc --arg gw "$GW_ID" --arg tid "$EXISTING_TARGET" --arg name "$GW_TARGET_NAME" --arg larn "$LAMBDA_ARN" --argjson tools "$TOOL_SCHEMA" \
    '{gatewayIdentifier:$gw, targetId:$tid, name:$name, description:"data-quality-deequ-agent tools",
      targetConfiguration:{mcp:{lambda:{lambdaArn:$larn, toolSchema:{inlinePayload:$tools}}}},
      credentialProviderConfigurations:[{credentialProviderType:"GATEWAY_IAM_ROLE"}]}')
  aws bedrock-agentcore-control update-gateway-target --region "$REGION" --cli-input-json "$UPDATE_PAYLOAD" >/dev/null
else
  echo "  create gateway target $GW_TARGET_NAME"
  TARGET_PAYLOAD=$(jq -nc --arg gw "$GW_ID" --arg name "$GW_TARGET_NAME" --arg larn "$LAMBDA_ARN" --argjson tools "$TOOL_SCHEMA" \
    '{gatewayIdentifier:$gw, name:$name, description:"data-quality-deequ-agent tools",
      targetConfiguration:{mcp:{lambda:{lambdaArn:$larn, toolSchema:{inlinePayload:$tools}}}},
      credentialProviderConfigurations:[{credentialProviderType:"GATEWAY_IAM_ROLE"}]}')
  aws bedrock-agentcore-control create-gateway-target --region "$REGION" --cli-input-json "$TARGET_PAYLOAD" >/dev/null
fi

aws lambda add-permission --function-name "$LAMBDA_NAME" --region "$REGION" \
  --statement-id agentcore-gw-invoke --action lambda:InvokeFunction \
  --principal bedrock-agentcore.amazonaws.com >/dev/null 2>&1 || true

# ─── 9. ECR repo + container build (linux/arm64) ────────────────────────────
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
  docker buildx inspect data-quality-deequ-agent-builder >/dev/null 2>&1 || docker buildx create --name data-quality-deequ-agent-builder --use >/dev/null
  docker buildx use data-quality-deequ-agent-builder >/dev/null
  docker buildx build --platform linux/arm64 -t "${ECR_URI}:latest" --push "$HERE/agent" >&2
else
  finch build --platform linux/arm64 -t "${ECR_URI}:latest" "$HERE/agent" >&2
  finch push "${ECR_URI}:latest" >&2
fi

# ─── 10. AgentCore Runtime ──────────────────────────────────────────────────
RT_ENV_VARS=$(jq -nc \
  --arg gw "$GW_URL" --arg secret "$SECRET_NAME" --arg target "$GW_TARGET_NAME" \
  --arg model "$MODEL_ID" --arg region "$REGION" \
  --arg bucket "$DATA_BUCKET" --arg rp "$REPORT_PREFIX" --arg ip "$INSIGHTS_PREFIX" \
  --arg db "$DQ_DATABASE" --arg tbl "$DQ_TABLE" \
  '{GATEWAY_URL:$gw, GATEWAY_CREDS_SECRET:$secret, GATEWAY_TARGET_NAME:$target,
    MODEL_ID:$model, AWS_REGION:$region,
    REPORT_BUCKET:$bucket, REPORT_PREFIX:$rp, DQ_INSIGHTS_PREFIX:$ip,
    DQ_DATABASE:$db, DQ_TABLE:$tbl}')
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

# ─── 11. Bridge Lambda + EventBridge rule ───────────────────────────────────
echo "  package bridge lambda"
cp "$HERE/bridge/lambda_function.py" "$BUILD/bridge.py"
(cd "$BUILD" && zip -q -j bridge.zip bridge.py)

BRIDGE_ENV=$(jq -nc \
  --arg rt "$RT_ARN" --arg secret "$SECRET_NAME" --arg dp "$DESIGNS_PREFIX" \
  '{Variables:{RUNTIME_ARN:$rt, GATEWAY_CREDS_SECRET:$secret, DESIGNS_PREFIX:$dp}}')

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

EB_PATTERN=$(jq -nc --arg bucket "$DATA_BUCKET" --arg dp "$DESIGNS_PREFIX" \
  '{source:["aws.s3"], "detail-type":["Object Created"],
    detail:{bucket:{name:[$bucket]}, object:{key:[{prefix:$dp}]}}}')

aws events put-rule --name "$EB_RULE_NAME" --region "$REGION" \
  --event-pattern "$EB_PATTERN" --state ENABLED >/dev/null
aws events put-targets --rule "$EB_RULE_NAME" --region "$REGION" \
  --targets "Id=1,Arn=${BRIDGE_ARN}" >/dev/null
aws lambda add-permission --function-name "$BRIDGE_NAME" --region "$REGION" \
  --statement-id eventbridge-invoke --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${REGION}:${ACCOUNT}:rule/${EB_RULE_NAME}" >/dev/null 2>&1 || true

cat <<EOF

[data-quality-deequ-agent] deployed
  Data bucket:         s3://$DATA_BUCKET
  Designs prefix:      $DESIGNS_PREFIX  (input trigger)
  Report prefix:       $REPORT_PREFIX
  Insights prefix:     $INSIGHTS_PREFIX
  Glue script:         $SCRIPT_URI
  Glue role:           $GLUE_ROLE_ARN
  Deequ Glue job:      $DEEQU_JOB_NAME
  DQ database:         $DQ_DATABASE
  DQ table:            $DQ_TABLE
  Athena workgroup:    $ATHENA_WORKGROUP  (out=$ATHENA_OUTPUT_LOCATION)
  Cognito user pool:   $USER_POOL_ID
  Cognito client:      $CLIENT_ID
  Test user:           $RUNTIME_USER  (creds in secret $SECRET_NAME)
  Tools Lambda:        $LAMBDA_ARN
  Gateway URL:         $GW_URL
  ECR image:           ${ECR_URI}:latest
  Runtime ARN:         $RT_ARN
  Bridge Lambda:       $BRIDGE_ARN
  EventBridge rule:    $EB_RULE_NAME

Live test (after data-architect-agent has produced a design file):
  aws s3 ls s3://$DATA_BUCKET/${DESIGNS_PREFIX} --region $REGION
  # the bridge Lambda fires automatically when designs/*.json lands; or:

Manual invoke:
  RUNTIME_ARN='$RT_ARN' SECRET_NAME='$SECRET_NAME' AWS_REGION='$REGION' \\
    python $HERE/scripts/invoke_agent.py s3://$DATA_BUCKET/${DESIGNS_PREFIX}violations.json
EOF
