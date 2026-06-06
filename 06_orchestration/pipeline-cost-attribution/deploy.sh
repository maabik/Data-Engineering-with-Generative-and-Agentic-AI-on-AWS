#!/usr/bin/env bash
# Deploy pipeline-cost-attribution.
#
# Creates:
#   - S3 bucket                     ${BUCKET}                 (cost output + tag mapping)
#   - IAM role (Lambda exec)        ${PREFIX}-lambda-role
#   - Lambda fn                     ${PREFIX}-tag-enforcer    (EventBridge driven)
#   - Lambda fn                     ${PREFIX}-cost-aggregator (scheduled)
#   - EventBridge rule              ${PREFIX}-create-events   (Glue/SFN/Athena create*)
#   - EventBridge rule              ${PREFIX}-daily           (cost aggregator schedule)
#   - Glue database                 ${GLUE_DB}
#   - Glue table                    costs (over s3://${BUCKET}/cost/)
#   - Athena workgroup              ${PREFIX}-wg
#
# Required env (defaults):
#   AWS_REGION        us-east-1
#   PREFIX            pipeline-cost
#   BUCKET            ${PREFIX}-${ACCOUNT}-${AWS_REGION}
#   GLUE_DB           pipeline_cost_attribution
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-pipeline-cost}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"
GLUE_DB="${GLUE_DB:-pipeline_cost_attribution}"

LAMBDA_ROLE="${PREFIX}-lambda-role"
TAG_FN="${PREFIX}-tag-enforcer"
AGG_FN="${PREFIX}-cost-aggregator"
WG_NAME="${PREFIX}-wg"
EVT_RULE="${PREFIX}-create-events"
SCHED_RULE="${PREFIX}-daily"
MAPPING_KEY="config/mapping.json"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

echo "[pipeline-cost-attribution] deploy region=$REGION account=$ACCOUNT bucket=$BUCKET"

# 1. S3 bucket -----------------------------------------------------------------
if ! aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
  echo "  create bucket $BUCKET"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
  fi
  aws s3api put-bucket-encryption --bucket "$BUCKET" --region "$REGION" \
    --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' >/dev/null
  aws s3api put-public-access-block --bucket "$BUCKET" --region "$REGION" \
    --public-access-block-configuration \
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true' >/dev/null
fi

# Upload sample mapping if missing
if ! aws s3api head-object --bucket "$BUCKET" --key "$MAPPING_KEY" --region "$REGION" >/dev/null 2>&1; then
  echo "  upload mapping config to s3://$BUCKET/$MAPPING_KEY"
  aws s3 cp "$HERE/sample-mapping.json" "s3://$BUCKET/$MAPPING_KEY" --region "$REGION" >/dev/null
fi

# 2. IAM role ------------------------------------------------------------------
LAMBDA_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

if ! aws iam get-role --role-name "$LAMBDA_ROLE" >/dev/null 2>&1; then
  echo "  create role $LAMBDA_ROLE"
  aws iam create-role --role-name "$LAMBDA_ROLE" \
    --assume-role-policy-document "$LAMBDA_TRUST" >/dev/null
fi
LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${LAMBDA_ROLE}"

LAMBDA_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"},
    {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:ListBucket"],
     "Resource":["arn:aws:s3:::${BUCKET}","arn:aws:s3:::${BUCKET}/*"]},
    {"Effect":"Allow","Action":["ce:GetCostAndUsage"],"Resource":"*"},
    {"Effect":"Allow","Action":[
        "glue:TagResource","glue:GetJob","glue:GetJobs",
        "states:TagResource","states:DescribeStateMachine","states:ListStateMachines",
        "athena:TagResource","athena:GetWorkGroup","athena:ListWorkGroups",
        "tag:GetResources","sts:GetCallerIdentity"
     ],"Resource":"*"}
  ]
}
JSON
)
aws iam put-role-policy --role-name "$LAMBDA_ROLE" \
  --policy-name "${PREFIX}-lambda-inline" --policy-document "$LAMBDA_POLICY" >/dev/null

# 3. Package Lambdas -----------------------------------------------------------
echo "  package tag-enforcer + cost-aggregator"
cp "$HERE/tag_enforcer.py" "$BUILD_DIR/tag_enforcer.py"
cp "$HERE/cost_aggregator.py" "$BUILD_DIR/cost_aggregator.py"
(cd "$BUILD_DIR" && zip -q tag.zip tag_enforcer.py && zip -q agg.zip cost_aggregator.py)

# 4. Create / update Lambdas ---------------------------------------------------
TAG_ENV=$(cat <<JSON
{"Variables":{"MAPPING_BUCKET":"${BUCKET}","MAPPING_KEY":"${MAPPING_KEY}","DEFAULT_TEAM":"unassigned","DEFAULT_BU":"unassigned","DEFAULT_ENV":"dev","REQUIRED_TAGS":"team,business_unit,pipeline_id,env"}}
JSON
)
AGG_ENV=$(cat <<JSON
{"Variables":{"OUTPUT_BUCKET":"${BUCKET}","OUTPUT_PREFIX":"cost/","DAYS_BACK":"30","GRANULARITY":"DAILY","METRIC":"UnblendedCost"}}
JSON
)

deploy_lambda() {
  local name="$1" zip="$2" handler="$3" env="$4" timeout="$5" mem="$6"
  if aws lambda get-function --function-name "$name" --region "$REGION" >/dev/null 2>&1; then
    echo "  update $name"
    aws lambda update-function-code --function-name "$name" --region "$REGION" \
      --zip-file "fileb://$BUILD_DIR/$zip" >/dev/null
    aws lambda wait function-updated --function-name "$name" --region "$REGION"
    aws lambda update-function-configuration --function-name "$name" --region "$REGION" \
      --environment "$env" --timeout "$timeout" --memory-size "$mem" >/dev/null
    aws lambda wait function-updated --function-name "$name" --region "$REGION"
  else
    echo "  create $name"
    for i in 1 2 3 4 5 6 7 8; do
      if aws lambda create-function --function-name "$name" --region "$REGION" \
          --runtime python3.12 --role "$LAMBDA_ROLE_ARN" \
          --handler "$handler" \
          --zip-file "fileb://$BUILD_DIR/$zip" \
          --timeout "$timeout" --memory-size "$mem" \
          --environment "$env" >/dev/null 2>/tmp/lambda-create.err; then
        break
      fi
      if grep -q "cannot be assumed" /tmp/lambda-create.err; then
        echo "    waiting for IAM propagation... ($i)"
        sleep 5
        continue
      fi
      cat /tmp/lambda-create.err >&2
      exit 1
    done
  fi
}

deploy_lambda "$TAG_FN" tag.zip "tag_enforcer.lambda_handler" "$TAG_ENV" 30 256
deploy_lambda "$AGG_FN" agg.zip "cost_aggregator.lambda_handler" "$AGG_ENV" 300 512

TAG_ARN="$(aws lambda get-function --function-name "$TAG_FN" --region "$REGION" --query 'Configuration.FunctionArn' --output text)"
AGG_ARN="$(aws lambda get-function --function-name "$AGG_FN" --region "$REGION" --query 'Configuration.FunctionArn' --output text)"

# 5. EventBridge rule for create events ---------------------------------------
EVT_PATTERN=$(cat <<JSON
{
  "source": ["aws.glue", "aws.states", "aws.athena"],
  "detail-type": ["AWS API Call via CloudTrail"],
  "detail": {
    "eventSource": ["glue.amazonaws.com", "states.amazonaws.com", "athena.amazonaws.com"],
    "eventName": ["CreateJob", "CreateStateMachine", "CreateWorkGroup", "UpdateJob"]
  }
}
JSON
)
aws events put-rule --name "$EVT_RULE" --region "$REGION" \
  --event-pattern "$EVT_PATTERN" --state ENABLED >/dev/null
aws lambda add-permission --function-name "$TAG_FN" --region "$REGION" \
  --statement-id evt-create-rule --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${REGION}:${ACCOUNT}:rule/${EVT_RULE}" >/dev/null 2>&1 || true
aws events put-targets --rule "$EVT_RULE" --region "$REGION" \
  --targets "Id=tag-enforcer,Arn=${TAG_ARN}" >/dev/null

# 6. EventBridge schedule for cost aggregator ---------------------------------
aws events put-rule --name "$SCHED_RULE" --region "$REGION" \
  --schedule-expression "rate(1 day)" --state ENABLED >/dev/null
aws lambda add-permission --function-name "$AGG_FN" --region "$REGION" \
  --statement-id evt-sched --action lambda:InvokeFunction \
  --principal events.amazonaws.com \
  --source-arn "arn:aws:events:${REGION}:${ACCOUNT}:rule/${SCHED_RULE}" >/dev/null 2>&1 || true
aws events put-targets --rule "$SCHED_RULE" --region "$REGION" \
  --targets "Id=cost-aggregator,Arn=${AGG_ARN}" >/dev/null

# 7. Glue database + table -----------------------------------------------------
if ! aws glue get-database --name "$GLUE_DB" --region "$REGION" >/dev/null 2>&1; then
  echo "  create glue database $GLUE_DB"
  aws glue create-database --region "$REGION" \
    --database-input "{\"Name\":\"${GLUE_DB}\"}" >/dev/null
fi

TABLE_INPUT=$(cat <<JSON
{
  "Name": "costs",
  "TableType": "EXTERNAL_TABLE",
  "Parameters": {
    "classification": "json",
    "projection.enabled": "true",
    "projection.ingest_date.type": "date",
    "projection.ingest_date.format": "yyyy-MM-dd",
    "projection.ingest_date.range": "2026-01-01,NOW",
    "projection.ingest_date.interval": "1",
    "projection.ingest_date.interval.unit": "DAYS",
    "storage.location.template": "s3://${BUCKET}/cost/ingest_date=\${ingest_date}/"
  },
  "PartitionKeys": [
    {"Name": "ingest_date", "Type": "string"}
  ],
  "StorageDescriptor": {
    "Columns": [
      {"Name": "usage_date", "Type": "string"},
      {"Name": "dimension", "Type": "string"},
      {"Name": "dimension_value", "Type": "string"},
      {"Name": "service", "Type": "string"},
      {"Name": "amount", "Type": "double"},
      {"Name": "unit", "Type": "string"},
      {"Name": "group_by_tag", "Type": "string"}
    ],
    "Location": "s3://${BUCKET}/cost/",
    "InputFormat": "org.apache.hadoop.mapred.TextInputFormat",
    "OutputFormat": "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat",
    "SerdeInfo": {
      "SerializationLibrary": "org.openx.data.jsonserde.JsonSerDe",
      "Parameters": {"ignore.malformed.json": "true"}
    }
  }
}
JSON
)
if aws glue get-table --database-name "$GLUE_DB" --name costs --region "$REGION" >/dev/null 2>&1; then
  aws glue update-table --database-name "$GLUE_DB" --region "$REGION" \
    --table-input "$TABLE_INPUT" >/dev/null
else
  aws glue create-table --database-name "$GLUE_DB" --region "$REGION" \
    --table-input "$TABLE_INPUT" >/dev/null
fi

# 8. Athena workgroup ----------------------------------------------------------
if ! aws athena get-work-group --work-group "$WG_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  create athena workgroup $WG_NAME"
  aws athena create-work-group --name "$WG_NAME" --region "$REGION" \
    --configuration "ResultConfiguration={OutputLocation=s3://${BUCKET}/athena-results/}" >/dev/null
fi

cat <<EOF

[pipeline-cost-attribution] deployed
  Bucket:        s3://$BUCKET
  Mapping:       s3://$BUCKET/$MAPPING_KEY
  Tag fn:        $TAG_FN
  Aggregator:    $AGG_FN
  Glue DB:       $GLUE_DB
  Glue table:    costs   (Athena: $GLUE_DB.costs)
  Workgroup:     $WG_NAME

Next steps:
  # Run cost aggregator on demand
  aws lambda invoke --function-name $AGG_FN --region $REGION /tmp/agg.json && cat /tmp/agg.json

  # Run a chargeback query
  aws athena start-query-execution \\
    --work-group $WG_NAME --region $REGION \\
    --query-string "\$(cat queries/cost_by_team.sql)" \\
    --result-configuration OutputLocation=s3://$BUCKET/athena-results/
EOF
