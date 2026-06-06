#!/usr/bin/env bash
# Data Engineering Orchestrator Agent — single-shot deploy.
#
# Provisions, idempotently:
#   - IAM role     data-engineering-orchestrator-agent-runtime-role  (with cross-region InvokeAgentRuntime
#                                     on every downstream agent ARN)
#   - ECR repo     data-engineering-orchestrator-agent + container build/push (linux/arm64)
#   - AgentCore    data_engineering_orchestrator_agent (Runtime) with AGENT_REGISTRY_JSON env var
#                  populated from currently-deployed downstream agents
#
# The orchestrator routes every data-engineering request to the right
# specialist agent shipped in this repo. It discovers downstream agents at
# deploy time by listing AgentCore runtimes in us-east-1 and us-west-2 (set DISCOVERY_REGIONS to override) and
# matching well-known runtime names.
#
# Skips AIDLC ceremony. Accepts all defaults.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-data-engineering-orchestrator-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

RT_ROLE="${PREFIX}-runtime-role"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="data_engineering_orchestrator_agent"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-sonnet-4-6}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[orchestrator] deploy region=$REGION account=$ACCOUNT"

# ─── 1. Discover downstream agents in both regions ───────────────────────────
# Map: <runtime-name> -> {region, description, payload_hint}.
# These are the well-known names produced by the deploy.sh of each
# specialist agent in chapters 05/06/12.
read -r -d '' DOWNSTREAM_SPEC <<'JSON' || true
{
  "data_profiling_agent": {
    "region": "us-east-1",
    "auth_secret": "data-profiling-agent/gateway-credentials",
    "description": "Data Profiling — DataBrew profile of S3 dataset; emits insights JSON.",
    "payload_hint": {"s3_uri": "s3://<bucket>/landing/<file>"}
  },
  "data_architect_agent": {
    "region": "us-east-1",
    "auth_secret": "data-architect-agent/gateway-credentials",
    "description": "Data Architect — consumes profiling insights, designs schema, runs PySpark ETL.",
    "payload_hint": {"insights_uri": "s3://<bucket>/insights/<file>.json"}
  },
  "data_quality_deequ_agent": {
    "region": "us-east-1",
    "auth_secret": "data-quality-deequ-agent/gateway-credentials",
    "description": "Data Quality (Deequ) — constraint suggestion+verification; emits quality JSON.",
    "payload_hint": {"design_uri": "s3://<bucket>/designs/<file>.json"}
  },
  "data_quality_agent": {
    "region": "us-east-1",
    "auth_secret": "data-quality-agent/gateway-credentials",
    "description": "DQ Remediation — investigates failed Deequ constraints; writes remediation plan.",
    "payload_hint": {"insights_uri": "s3://<bucket>/dq-insights/<file>.json"}
  },
  "api_integration_agent": {
    "region": "us-east-1",
    "auth_secret": "api-integration-agent/gateway-credentials",
    "description": "API Integration — generates+deploys Lambda from API spec; smoke-tests.",
    "payload_hint": {"dataset": "<name>", "description": "<plain English>", "schedule": "rate(1 hour)"}
  },
  "log_analysis_agent": {
    "region": "us-east-1",
    "auth_secret": "log-analysis-agent/gateway-credentials",
    "description": "Log Analysis — root-causes pipeline failures from logs; emits remediation.",
    "payload_hint": {"run_uri": "s3://<bucket>/pipeline-runs/<id>.json"}
  },
  "text_to_dag_agent": {
    "region": "us-east-1",
    "auth_secret": "text-to-dag-agent/gateway-credentials",
    "description": "Text-to-DAG — NL pipeline -> Step Functions ASL or Airflow DAG.",
    "payload_hint": {"prompt": "<pipeline desc>", "target": "stepfunctions|airflow"}
  },
  "sql_evaluations_agent": {
    "region": "us-east-1",
    "description": "SQL Evaluations — reviews SQL for errors, optimization, security.",
    "payload_hint": {"sql": "<SQL>", "context": "", "dialect": "ansi"}
  },
  "sales_reporting_agent": {
    "region": "us-east-1",
    "description": "Sales Reporting — NL questions against Redshift sales schema.",
    "payload_hint": {"prompt": "<sales question>"}
  },
  "redshift_mcp_server_agentcore": {
    "region": "us-east-1",
    "description": "Redshift MCP Server (MCP-over-HTTP). Capability only — do not invoke as JSON.",
    "payload_hint": {}
  }
}
JSON

# Build the AGENT_REGISTRY_JSON dict by joining DOWNSTREAM_SPEC with the
# actual deployed runtimes in each region (only include READY ones that
# match a name in the spec). We also collect their ARNs so we can scope
# the runtime role's InvokeAgentRuntime IAM permissions to just those.
REGISTRY_JSON='{}'
INVOKE_ARNS='[]'

for r in us-east-1 us-west-2; do
  RT_LIST="$(aws bedrock-agentcore-control list-agent-runtimes --region "$r" \
    --query 'agentRuntimes[].{name:agentRuntimeName,arn:agentRuntimeArn,status:status}' \
    --output json 2>/dev/null || echo '[]')"
  echo "  region $r: $(echo "$RT_LIST" | jq 'length') runtime(s)"
  REGISTRY_JSON="$(jq -nc \
    --argjson reg  "$REGISTRY_JSON" \
    --argjson rts  "$RT_LIST" \
    --argjson spec "$DOWNSTREAM_SPEC" \
    --arg     region "$r" '
    $reg + (reduce ($rts[] | select(.name != null and .status == "READY")) as $rt ({};
      ($rt.name) as $n |
      if ($spec[$n] != null)
      then . + {($n): ($spec[$n] + {arn: $rt.arn, region: $region})}
      else . end
    ))')"
  INVOKE_ARNS="$(jq -nc \
    --argjson arns "$INVOKE_ARNS" \
    --argjson rts  "$RT_LIST" \
    --argjson spec "$DOWNSTREAM_SPEC" '
    $arns + [($rts[] | select(.name != null and .status == "READY") | . as $rt | select($spec[$rt.name] != null) | $rt.arn)]')"
done

N_AGENTS="$(echo "$REGISTRY_JSON" | jq 'length')"

echo "  discovered $N_AGENTS downstream agent(s):"
echo "$REGISTRY_JSON" | jq -r 'to_entries[] | "    - \(.key) (\(.value.region))"'

if [ "$N_AGENTS" -eq 0 ]; then
  echo "WARNING: no downstream agents discovered. The orchestrator will deploy"
  echo "         but will have nothing to route to. Deploy specialist agents"
  echo "         first, then re-run this script." >&2
fi

# ─── 2. IAM runtime role ─────────────────────────────────────────────────────
RT_TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT}"},"ArnLike":{"aws:SourceArn":"arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT}:*"}}}]}
JSON
)

if ! aws iam get-role --role-name "$RT_ROLE" >/dev/null 2>&1; then
  echo "  create role $RT_ROLE"
  aws iam create-role --role-name "$RT_ROLE" --assume-role-policy-document "$RT_TRUST" >/dev/null
else
  aws iam update-assume-role-policy --role-name "$RT_ROLE" --policy-document "$RT_TRUST" >/dev/null
fi
RT_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${RT_ROLE}"

# Build inline policy: include InvokeAgentRuntime on the discovered ARNs.
# Fall back to "*" if the ARN list is empty (so the role still works after
# a fresh re-deploy of downstream agents creates new ARN suffixes).
if [ "$(echo "$INVOKE_ARNS" | jq 'length')" -gt 0 ]; then
  INVOKE_RESOURCE="$(echo "$INVOKE_ARNS" | jq -c '. + (map(. + "/*"))')"
else
  INVOKE_RESOURCE='["*"]'
fi

RT_POLICY=$(jq -nc --argjson invoke "$INVOKE_RESOURCE" '
{
  Version: "2012-10-17",
  Statement: [
    {Effect:"Allow", Action:[
      "logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents",
      "logs:DescribeLogStreams","logs:DescribeLogGroups"
    ], Resource:"*"},
    {Effect:"Allow", Action:[
      "bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"
    ], Resource:"*"},
    {Effect:"Allow", Action:[
      "bedrock-agentcore:InvokeAgentRuntime"
    ], Resource: $invoke},
    {Effect:"Allow", Action:[
      "bedrock-agentcore-control:ListAgentRuntimes",
      "bedrock-agentcore-control:GetAgentRuntime"
    ], Resource:"*"},
    {Effect:"Allow", Action:[
      "secretsmanager:GetSecretValue"
    ], Resource:[
      "arn:aws:secretsmanager:*:*:secret:data-profiling-agent/gateway-credentials*",
      "arn:aws:secretsmanager:*:*:secret:data-architect-agent/gateway-credentials*",
      "arn:aws:secretsmanager:*:*:secret:data-quality-deequ-agent/gateway-credentials*",
      "arn:aws:secretsmanager:*:*:secret:data-quality-agent/gateway-credentials*",
      "arn:aws:secretsmanager:*:*:secret:api-integration-agent/gateway-credentials*",
      "arn:aws:secretsmanager:*:*:secret:log-analysis-agent/gateway-credentials*",
      "arn:aws:secretsmanager:*:*:secret:text-to-dag-agent/gateway-credentials*"
    ]},
    {Effect:"Allow", Action:[
      "cognito-idp:InitiateAuth"
    ], Resource:"arn:aws:cognito-idp:*:*:userpool/*"},
    {Effect:"Allow", Action:[
      "glue:GetJobRun","glue:GetJobRuns",
      "databrew:DescribeJobRun","databrew:ListJobRuns",
      "states:DescribeExecution","states:GetExecutionHistory",
      "logs:FilterLogEvents","logs:DescribeLogStreams"
    ], Resource:"*"},
    {Effect:"Allow", Action:[
      "ecr:BatchGetImage","ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability","ecr:GetAuthorizationToken"
    ], Resource:"*"},
    {Effect:"Allow", Action:[
      "xray:PutTraceSegments","xray:PutTelemetryRecords",
      "xray:GetSamplingRules","xray:GetSamplingTargets",
      "xray:GetSamplingStatisticSummaries"
    ], Resource:"*"}
  ]
}')

aws iam put-role-policy --role-name "$RT_ROLE" --policy-name inline --policy-document "$RT_POLICY" >/dev/null

# Wait for IAM propagation before the runtime tries to assume the role.
sleep 8

# ─── 3. ECR repo + container build (linux/arm64) ─────────────────────────────
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
  docker buildx inspect data-engineering-orchestrator-agent-builder >/dev/null 2>&1 || docker buildx create --name data-engineering-orchestrator-agent-builder --use >/dev/null
  docker buildx use data-engineering-orchestrator-agent-builder >/dev/null
  docker buildx build --platform linux/arm64 -t "${ECR_URI}:latest" --push "$HERE/agent" >&2
else
  finch build --platform linux/arm64 -t "${ECR_URI}:latest" "$HERE/agent" >&2
  finch push "${ECR_URI}:latest" >&2
fi

# ─── 4. AgentCore Runtime ────────────────────────────────────────────────────
RT_ENV_VARS=$(jq -nc \
  --arg model "$MODEL_ID" --arg region "$REGION" \
  --arg registry "$REGISTRY_JSON" \
  '{MODEL_ID:$model, AWS_REGION:$region, AGENT_REGISTRY_JSON:$registry}')

EXISTING_RT="$(aws bedrock-agentcore-control list-agent-runtimes --no-paginate --region "$REGION" \
  --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" \
  --output text 2>/dev/null || true)"

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

# ─── 5. SSM parameter for downstream callers ─────────────────────────────────
aws ssm put-parameter --name "/${PREFIX}/agent_runtime_arn" --value "$RUNTIME_ARN" --type String --overwrite --region "$REGION" >/dev/null

printf '\n✅ data-engineering-orchestrator-agent deployed\n\n'
printf '  region:            %s\n' "$REGION"
printf '  agent runtime ARN: %s\n' "$RUNTIME_ARN"
printf '  registry size:     %s downstream agent(s)\n\n' "$N_AGENTS"
printf 'Direct invoke:\n  AGENT_RUNTIME_ARN=%s python3 scripts/invoke_runtime.py "Profile s3://my-bucket/landing/orders.csv"\n' "$RUNTIME_ARN"
