#!/usr/bin/env bash
# Online Agent Evaluation — single-shot deploy.
#
# Provisions, idempotently, in BOTH us-east-1 and us-west-2:
#   - S3 bucket          oae-${ACCOUNT}-${REGION}        (verdicts + registry)
#   - DynamoDB table     oae-rates                       (us-east-1 only)
#   - IAM role           oae-evaluator-role              (per region)
#   - Lambda             oae-evaluator                   (per region)
#   - Subscription filters on every active agent's log group, sample-rate
#     gated inside the Lambda.
#
# Re-run after deploying any new agent so the evaluator picks it up.
set -euo pipefail

PRIMARY_REGION="${PRIMARY_REGION:-us-east-1}"
SECONDARY_REGION="${SECONDARY_REGION:-us-west-2}"
PREFIX="${PREFIX:-oae}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

LAMBDA_NAME="${PREFIX}-evaluator"
BATCH_LAMBDA_NAME="${PREFIX}-batch-eval"
ROLE_NAME="${PREFIX}-evaluator-role"
BATCH_ROLE_NAME="${PREFIX}-batch-eval-role"
EVAL_SERVICE_ROLE_NAME="${PREFIX}-bedrock-eval-role"
SCHEDULE_NAME="${PREFIX}-batch-daily"
RATES_TABLE="${PREFIX}-rates"
TRACES_TABLE="${PREFIX}-traces"
SAMPLE_RATE="${SAMPLE_RATE:-0.05}"
JUDGE_MODEL_ID="${JUDGE_MODEL_ID:-us.anthropic.claude-sonnet-4-6}"
SUB_FILTER_NAME="${PREFIX}-sub"
# Match-all pattern. The Lambda parses each log event and ignores anything
# that isn't an entrypoint request/response pair, so a JSON-shape filter at
# this level isn't needed. The dollar cost is dominated by judge calls
# (which are gated by SAMPLE_RATE), not by Lambda invocations.
SUB_FILTER_PATTERN=''

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT

echo "[online-agent-evaluation] account=$ACCOUNT primary=$PRIMARY_REGION secondary=$SECONDARY_REGION"

# ─── 1. Per-region S3 verdicts buckets ──────────────────────────────────────
ensure_bucket() {
  local region="$1"
  local bucket="${PREFIX}-${ACCOUNT}-${region}"
  if ! aws s3api head-bucket --bucket "$bucket" --region "$region" >/dev/null 2>&1; then
    echo "  [$region] create verdicts bucket $bucket" >&2
    if [ "$region" = "us-east-1" ]; then
      aws s3api create-bucket --bucket "$bucket" --region "$region" >/dev/null
    else
      aws s3api create-bucket --bucket "$bucket" --region "$region" \
        --create-bucket-configuration "LocationConstraint=$region" >/dev/null
    fi
  fi
  aws s3api put-bucket-versioning --bucket "$bucket" --region "$region" \
    --versioning-configuration Status=Enabled >/dev/null
  echo "$bucket"
}

PRIMARY_BUCKET="$(ensure_bucket "$PRIMARY_REGION")"
SECONDARY_BUCKET="$(ensure_bucket "$SECONDARY_REGION")"

# ─── 2. DynamoDB rates table (single region — read from each Lambda) ────────
if ! aws dynamodb describe-table --table-name "$RATES_TABLE" --region "$PRIMARY_REGION" >/dev/null 2>&1; then
  echo "  create rates table $RATES_TABLE in $PRIMARY_REGION"
  aws dynamodb create-table --table-name "$RATES_TABLE" --region "$PRIMARY_REGION" \
    --attribute-definitions AttributeName=agent_name,AttributeType=S \
    --key-schema AttributeName=agent_name,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST >/dev/null
  aws dynamodb wait table-exists --table-name "$RATES_TABLE" --region "$PRIMARY_REGION"
fi

# Trace stitching table — one row per in-flight traceId, TTL-cleaned. Created
# in BOTH regions; the evaluator Lambda in each region uses its own copy
# (cross-region DDB latency would dominate the trace pairing path).
ensure_traces_table() {
  local region="$1"
  if ! aws dynamodb describe-table --table-name "$TRACES_TABLE" --region "$region" >/dev/null 2>&1; then
    echo "  [$region] create traces table $TRACES_TABLE"
    aws dynamodb create-table --table-name "$TRACES_TABLE" --region "$region" \
      --attribute-definitions AttributeName=trace_id,AttributeType=S \
      --key-schema AttributeName=trace_id,KeyType=HASH \
      --billing-mode PAY_PER_REQUEST >/dev/null
    aws dynamodb wait table-exists --table-name "$TRACES_TABLE" --region "$region"
  fi
  # TTL: idempotent — describe-time-to-live returns the current state and
  # update-time-to-live errors if the desired state already matches, so guard.
  current="$(aws dynamodb describe-time-to-live --table-name "$TRACES_TABLE" --region "$region" \
    --query 'TimeToLiveDescription.TimeToLiveStatus' --output text 2>/dev/null || echo "")"
  if [ "$current" != "ENABLED" ] && [ "$current" != "ENABLING" ]; then
    aws dynamodb update-time-to-live --table-name "$TRACES_TABLE" --region "$region" \
      --time-to-live-specification 'Enabled=true,AttributeName=ttl' >/dev/null
  fi
}
ensure_traces_table "$PRIMARY_REGION"
ensure_traces_table "$SECONDARY_REGION"

# ─── 3. IAM role for the evaluator Lambda ───────────────────────────────────
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "  create role $ROLE_NAME"
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST" >/dev/null
fi

POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"},
  {"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],"Resource":"*"},
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:ListBucket"],"Resource":[
    "arn:aws:s3:::${PREFIX}-${ACCOUNT}-${PRIMARY_REGION}",   "arn:aws:s3:::${PREFIX}-${ACCOUNT}-${PRIMARY_REGION}/*",
    "arn:aws:s3:::${PREFIX}-${ACCOUNT}-${SECONDARY_REGION}", "arn:aws:s3:::${PREFIX}-${ACCOUNT}-${SECONDARY_REGION}/*"
  ]},
  {"Effect":"Allow","Action":["cloudwatch:PutMetricData"],"Resource":"*"},
  {"Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:Query"],"Resource":"arn:aws:dynamodb:${PRIMARY_REGION}:${ACCOUNT}:table/${RATES_TABLE}"},
  {"Effect":"Allow","Action":["dynamodb:GetItem","dynamodb:PutItem","dynamodb:UpdateItem","dynamodb:DeleteItem"],"Resource":[
    "arn:aws:dynamodb:${PRIMARY_REGION}:${ACCOUNT}:table/${TRACES_TABLE}",
    "arn:aws:dynamodb:${SECONDARY_REGION}:${ACCOUNT}:table/${TRACES_TABLE}"
  ]}
]}
JSON
)
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name inline --policy-document "$POLICY" >/dev/null
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${ROLE_NAME}"

sleep 8 # IAM propagation

# ─── 4. Package Lambda (single zip used by both regions) ────────────────────
echo "  package evaluator lambda"
cp "$HERE/evaluator/lambda_function.py" "$BUILD/lambda_function.py"
cp "$HERE/evaluator/judge_prompt.py"    "$BUILD/judge_prompt.py"
(cd "$BUILD" && zip -q lambda.zip lambda_function.py judge_prompt.py)

# ─── 5. Per-region Lambda + permission for CW Logs to invoke ────────────────
deploy_lambda() {
  local region="$1" bucket="$2"
  local env
  env=$(jq -nc \
    --arg rate "$SAMPLE_RATE" --arg model "$JUDGE_MODEL_ID" \
    --arg bucket "$bucket" --arg table "$RATES_TABLE" --arg traces "$TRACES_TABLE" \
    '{Variables:{
        SAMPLE_RATE:$rate,
        JUDGE_MODEL_ID:$model,
        VERDICTS_BUCKET:$bucket,
        SAMPLE_RATE_TABLE:$table,
        TRACES_TABLE:$traces,
        REGISTRY_KEY:"registry.json",
        METRIC_NAMESPACE:"OnlineAgentEvaluation"
      }}')

  if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$region" >/dev/null 2>&1; then
    echo "  [$region] update lambda code"
    aws lambda update-function-code --function-name "$LAMBDA_NAME" --region "$region" \
      --zip-file "fileb://$BUILD/lambda.zip" >/dev/null
    aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$region"
    aws lambda update-function-configuration --function-name "$LAMBDA_NAME" --region "$region" \
      --environment "$env" --timeout 60 --memory-size 512 >/dev/null
    aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$region"
  else
    echo "  [$region] create lambda"
    for i in 1 2 3 4 5 6; do
      if aws lambda create-function --function-name "$LAMBDA_NAME" --region "$region" \
          --runtime python3.12 --role "$ROLE_ARN" \
          --handler lambda_function.lambda_handler \
          --zip-file "fileb://$BUILD/lambda.zip" \
          --timeout 60 --memory-size 512 \
          --environment "$env" >/dev/null 2>/tmp/oae.err; then
        break
      fi
      if grep -q "cannot be assumed" /tmp/oae.err; then
        echo "    waiting for IAM... ($i)"; sleep 5; continue
      fi
      cat /tmp/oae.err >&2; exit 1
    done
  fi

  # CloudWatch Logs needs explicit permission to invoke the Lambda from this
  # account. Idempotent: ignore if statement already exists.
  aws lambda add-permission --function-name "$LAMBDA_NAME" --region "$region" \
    --statement-id cwlogs-invoke --action lambda:InvokeFunction \
    --principal "logs.${region}.amazonaws.com" \
    --source-account "$ACCOUNT" >/dev/null 2>&1 || true
}

deploy_lambda "$PRIMARY_REGION"   "$PRIMARY_BUCKET"
deploy_lambda "$SECONDARY_REGION" "$SECONDARY_BUCKET"

# ─── 6. Discover agents and upload registry to each region's bucket ─────────
echo "  discover active agents"
REGISTRY_FILE="$BUILD/registry.json"
python3 "$HERE/scripts/discover_agents.py" \
  --regions "$PRIMARY_REGION" "$SECONDARY_REGION" \
  > "$REGISTRY_FILE"

aws s3 cp "$REGISTRY_FILE" "s3://${PRIMARY_BUCKET}/registry.json"   --region "$PRIMARY_REGION"   >/dev/null
aws s3 cp "$REGISTRY_FILE" "s3://${SECONDARY_BUCKET}/registry.json" --region "$SECONDARY_REGION" >/dev/null

# ─── 7. Subscribe the regional Lambda to each agent's log group ─────────────
subscribe_one() {
  local region="$1" log_group="$2"
  local lambda_arn="arn:aws:lambda:${region}:${ACCOUNT}:function:${LAMBDA_NAME}"
  # put-subscription-filter is upsert-by-name, so re-running is safe.
  aws logs put-subscription-filter --region "$region" \
    --log-group-name "$log_group" \
    --filter-name "$SUB_FILTER_NAME" \
    --filter-pattern "$SUB_FILTER_PATTERN" \
    --destination-arn "$lambda_arn" >/dev/null
}

echo "  attach subscription filters to discovered agents"
TOTAL_SUBSCRIBED=0
while IFS=$'\t' read -r region log_group name; do
  [ -z "$log_group" ] && continue
  if subscribe_one "$region" "$log_group" 2>/tmp/oae.sub.err; then
    echo "    [$region] $name -> $log_group"
    TOTAL_SUBSCRIBED=$((TOTAL_SUBSCRIBED + 1))
  else
    echo "    [$region] $name SKIPPED ($(tr -d '\n' </tmp/oae.sub.err))"
  fi
done < <(jq -r '.agents[] | [.region, .log_group, .name] | @tsv' "$REGISTRY_FILE")

# ─── 7b. Batch layer: Bedrock Evaluations service role ──────────────────────
EVAL_TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT}"},"ArnLike":{"aws:SourceArn":"arn:aws:bedrock:${PRIMARY_REGION}:${ACCOUNT}:evaluation-job/*"}}}]}
JSON
)
if ! aws iam get-role --role-name "$EVAL_SERVICE_ROLE_NAME" >/dev/null 2>&1; then
  echo "  create bedrock evaluation service role $EVAL_SERVICE_ROLE_NAME"
  aws iam create-role --role-name "$EVAL_SERVICE_ROLE_NAME" --assume-role-policy-document "$EVAL_TRUST" >/dev/null
else
  aws iam update-assume-role-policy --role-name "$EVAL_SERVICE_ROLE_NAME" --policy-document "$EVAL_TRUST" >/dev/null
fi
EVAL_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["s3:GetObject","s3:ListBucket"],"Resource":["arn:aws:s3:::${PRIMARY_BUCKET}","arn:aws:s3:::${PRIMARY_BUCKET}/*"]},
  {"Effect":"Allow","Action":["s3:PutObject"],"Resource":"arn:aws:s3:::${PRIMARY_BUCKET}/eval-output/*"},
  {"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],"Resource":"*"}
]}
JSON
)
aws iam put-role-policy --role-name "$EVAL_SERVICE_ROLE_NAME" --policy-name inline --policy-document "$EVAL_POLICY" >/dev/null
EVAL_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${EVAL_SERVICE_ROLE_NAME}"

# ─── 7c. Batch layer: Lambda execution role ─────────────────────────────────
BATCH_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
if ! aws iam get-role --role-name "$BATCH_ROLE_NAME" >/dev/null 2>&1; then
  echo "  create batch lambda role $BATCH_ROLE_NAME"
  aws iam create-role --role-name "$BATCH_ROLE_NAME" --assume-role-policy-document "$BATCH_TRUST" >/dev/null
fi
BATCH_POLICY=$(cat <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"},
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:ListBucket","s3:DeleteObject"],"Resource":["arn:aws:s3:::${PRIMARY_BUCKET}","arn:aws:s3:::${PRIMARY_BUCKET}/*"]},
  {"Effect":"Allow","Action":["bedrock:CreateEvaluationJob","bedrock:GetEvaluationJob","bedrock:ListEvaluationJobs"],"Resource":"*"},
  {"Effect":"Allow","Action":["iam:PassRole"],"Resource":"${EVAL_ROLE_ARN}","Condition":{"StringEquals":{"iam:PassedToService":"bedrock.amazonaws.com"}}}
]}
JSON
)
aws iam put-role-policy --role-name "$BATCH_ROLE_NAME" --policy-name inline --policy-document "$BATCH_POLICY" >/dev/null
BATCH_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${BATCH_ROLE_NAME}"

sleep 8

# ─── 7d. Batch layer: Lambda ────────────────────────────────────────────────
echo "  package batch-eval lambda"
cp "$HERE/batch_eval/lambda_function.py" "$BUILD/batch_lambda.py"
(cd "$BUILD" && cp batch_lambda.py lambda_function.py && zip -q batch.zip lambda_function.py && rm lambda_function.py)

BATCH_ENV=$(jq -nc \
  --arg bucket "$PRIMARY_BUCKET" \
  --arg role "$EVAL_ROLE_ARN" \
  --arg model "arn:aws:bedrock:${PRIMARY_REGION}:${ACCOUNT}:inference-profile/${BATCH_JUDGE_PROFILE:-us.anthropic.claude-haiku-4-5-20251001-v1:0}" \
  '{Variables:{
      VERDICTS_BUCKET:$bucket,
      EVAL_ROLE_ARN:$role,
      JUDGE_MODEL_ID:$model,
      REGISTRY_KEY:"registry.json",
      MIN_ROWS_PER_JOB:"1"
    }}')

if aws lambda get-function --function-name "$BATCH_LAMBDA_NAME" --region "$PRIMARY_REGION" >/dev/null 2>&1; then
  echo "  [batch] update lambda code"
  aws lambda update-function-code --function-name "$BATCH_LAMBDA_NAME" --region "$PRIMARY_REGION" \
    --zip-file "fileb://$BUILD/batch.zip" >/dev/null
  aws lambda wait function-updated --function-name "$BATCH_LAMBDA_NAME" --region "$PRIMARY_REGION"
  aws lambda update-function-configuration --function-name "$BATCH_LAMBDA_NAME" --region "$PRIMARY_REGION" \
    --environment "$BATCH_ENV" --timeout 300 --memory-size 512 >/dev/null
  aws lambda wait function-updated --function-name "$BATCH_LAMBDA_NAME" --region "$PRIMARY_REGION"
else
  echo "  [batch] create lambda"
  for i in 1 2 3 4 5 6; do
    if aws lambda create-function --function-name "$BATCH_LAMBDA_NAME" --region "$PRIMARY_REGION" \
        --runtime python3.12 --role "$BATCH_ROLE_ARN" \
        --handler lambda_function.lambda_handler \
        --zip-file "fileb://$BUILD/batch.zip" \
        --timeout 300 --memory-size 512 \
        --environment "$BATCH_ENV" >/dev/null 2>/tmp/oae.batch.err; then
      break
    fi
    if grep -q "cannot be assumed" /tmp/oae.batch.err; then
      echo "    waiting for IAM... ($i)"; sleep 5; continue
    fi
    cat /tmp/oae.batch.err >&2; exit 1
  done
fi
BATCH_LAMBDA_ARN="arn:aws:lambda:${PRIMARY_REGION}:${ACCOUNT}:function:${BATCH_LAMBDA_NAME}"

# ─── 7e. Batch layer: EventBridge daily schedule ────────────────────────────
# Use the EventBridge `events` API (rules) — Schedule API would also work.
RULE_PATTERN='cron(15 6 * * ? *)'  # 06:15 UTC daily, ~yesterday boundary
if ! aws events describe-rule --name "$SCHEDULE_NAME" --region "$PRIMARY_REGION" >/dev/null 2>&1; then
  echo "  create schedule rule $SCHEDULE_NAME"
  aws events put-rule --name "$SCHEDULE_NAME" --region "$PRIMARY_REGION" \
    --schedule-expression "$RULE_PATTERN" --state ENABLED >/dev/null
fi
aws events put-targets --rule "$SCHEDULE_NAME" --region "$PRIMARY_REGION" \
  --targets "Id=batch,Arn=${BATCH_LAMBDA_ARN}" >/dev/null
aws lambda add-permission --function-name "$BATCH_LAMBDA_NAME" --region "$PRIMARY_REGION" \
  --statement-id eventbridge-invoke --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${PRIMARY_REGION}:${ACCOUNT}:rule/${SCHEDULE_NAME}" \
  >/dev/null 2>&1 || true

# ─── 8. SSM parameters for ops scripts ──────────────────────────────────────
aws ssm put-parameter --region "$PRIMARY_REGION" --name "/${PREFIX}/lambda_arn" \
  --value "arn:aws:lambda:${PRIMARY_REGION}:${ACCOUNT}:function:${LAMBDA_NAME}" \
  --type String --overwrite >/dev/null
aws ssm put-parameter --region "$PRIMARY_REGION" --name "/${PREFIX}/verdicts_bucket" \
  --value "$PRIMARY_BUCKET" --type String --overwrite >/dev/null
aws ssm put-parameter --region "$PRIMARY_REGION" --name "/${PREFIX}/rates_table" \
  --value "$RATES_TABLE" --type String --overwrite >/dev/null
aws ssm put-parameter --region "$PRIMARY_REGION" --name "/${PREFIX}/batch_lambda_arn" \
  --value "$BATCH_LAMBDA_ARN" --type String --overwrite >/dev/null

printf '\n✅ online-agent-evaluation deployed\n\n'
printf '  primary region:    %s   bucket: s3://%s\n' "$PRIMARY_REGION"   "$PRIMARY_BUCKET"
printf '  secondary region:  %s   bucket: s3://%s\n' "$SECONDARY_REGION" "$SECONDARY_BUCKET"
printf '  rates table:       %s\n' "$RATES_TABLE"
printf '  batch lambda:      %s   schedule: %s\n' "$BATCH_LAMBDA_NAME" "$RULE_PATTERN"
printf '  sample rate:       %s   judge: %s\n' "$SAMPLE_RATE" "$JUDGE_MODEL_ID"
printf '  agents subscribed: %s\n\n' "$TOTAL_SUBSCRIBED"
printf 'List subscriptions:\n  python3 scripts/list_evaluated_agents.py\n\n'
printf 'Bump rate for one agent:\n  python3 scripts/set_sample_rate.py <agent_name> 0.20\n'
