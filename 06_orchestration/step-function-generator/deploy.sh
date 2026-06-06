#!/usr/bin/env bash
# Deploy step-function-generator.
#
# Creates:
#   - S3 bucket                          ${BUCKET}
#   - IAM role  (Lambda exec)            ${PREFIX}-lambda-role
#   - IAM role  (state-machine exec)     ${PREFIX}-sfn-role
#   - Lambda function                    ${PREFIX}-fn
#   - S3 -> Lambda notification on prefix prompts/
#
# Required env (defaults):
#   AWS_REGION   us-east-1
#   PREFIX       sfn-gen
#   BUCKET       ${PREFIX}-${ACCOUNT}-${AWS_REGION}
#   MODEL_ID     us.anthropic.claude-haiku-4-5-20251001-v1:0
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-sfn-gen}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"
MODEL_ID="${MODEL_ID:-us.anthropic.claude-haiku-4-5-20251001-v1:0}"

LAMBDA_NAME="${PREFIX}-fn"
LAMBDA_ROLE="${PREFIX}-lambda-role"
SFN_ROLE="${PREFIX}-sfn-role"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT

echo "[step-function-generator] deploy region=$REGION account=$ACCOUNT bucket=$BUCKET"

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

# 2. IAM roles -----------------------------------------------------------------
LAMBDA_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
SFN_TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"states.amazonaws.com"},"Action":"sts:AssumeRole"}]}'

ensure_role() {
  local name="$1" trust="$2"
  if ! aws iam get-role --role-name "$name" >/dev/null 2>&1; then
    echo "  create role $name"
    aws iam create-role --role-name "$name" \
      --assume-role-policy-document "$trust" >/dev/null
  fi
}
ensure_role "$LAMBDA_ROLE" "$LAMBDA_TRUST"
ensure_role "$SFN_ROLE" "$SFN_TRUST"

LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${LAMBDA_ROLE}"
SFN_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${SFN_ROLE}"

LAMBDA_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"},
    {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],"Resource":"arn:aws:s3:::${BUCKET}/*"},
    {"Effect":"Allow","Action":["bedrock:InvokeModel"],"Resource":[
      "arn:aws:bedrock:*::foundation-model/anthropic.*",
      "arn:aws:bedrock:*:${ACCOUNT}:inference-profile/*"
    ]},
    {"Effect":"Allow","Action":["states:CreateStateMachine","states:UpdateStateMachine","states:DescribeStateMachine"],"Resource":"arn:aws:states:${REGION}:${ACCOUNT}:stateMachine:${PREFIX}*"},
    {"Effect":"Allow","Action":["iam:PassRole"],"Resource":"${SFN_ROLE_ARN}",
     "Condition":{"StringEquals":{"iam:PassedToService":"states.amazonaws.com"}}},
    {"Effect":"Allow","Action":["sts:GetCallerIdentity"],"Resource":"*"}
  ]
}
JSON
)
SFN_POLICY=$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect":"Allow","Action":["logs:CreateLogDelivery","logs:GetLogDelivery","logs:UpdateLogDelivery","logs:DeleteLogDelivery","logs:ListLogDeliveries","logs:PutResourcePolicy","logs:DescribeResourcePolicies","logs:DescribeLogGroups"],"Resource":"*"}
  ]
}
JSON
)

aws iam put-role-policy --role-name "$LAMBDA_ROLE" \
  --policy-name "${PREFIX}-lambda-inline" --policy-document "$LAMBDA_POLICY" >/dev/null
aws iam put-role-policy --role-name "$SFN_ROLE" \
  --policy-name "${PREFIX}-sfn-inline" --policy-document "$SFN_POLICY" >/dev/null

# 3. Lambda package ------------------------------------------------------------
echo "  package lambda"
cp "$HERE/step-function-lambda.py" "$BUILD_DIR/lambda_function.py"
(cd "$BUILD_DIR" && zip -q lambda.zip lambda_function.py)

# 4. Lambda create / update ----------------------------------------------------
ENV_VARS="Variables={BEDROCK_MODEL_ID=${MODEL_ID},STATE_MACHINE_ROLE_ARN=${SFN_ROLE_ARN},STATE_MACHINE_NAME_PREFIX=${PREFIX},INPUT_PREFIX=prompts/,OUTPUT_PREFIX=generated/}"

if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  update lambda code"
  aws lambda update-function-code --function-name "$LAMBDA_NAME" --region "$REGION" \
    --zip-file "fileb://$BUILD_DIR/lambda.zip" >/dev/null
  aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$REGION"
  aws lambda update-function-configuration --function-name "$LAMBDA_NAME" --region "$REGION" \
    --environment "$ENV_VARS" --timeout 60 --memory-size 512 >/dev/null
  aws lambda wait function-updated --function-name "$LAMBDA_NAME" --region "$REGION"
else
  echo "  create lambda"
  # IAM propagation can lag. Retry briefly.
  for i in 1 2 3 4 5 6 7 8; do
    if aws lambda create-function --function-name "$LAMBDA_NAME" --region "$REGION" \
        --runtime python3.12 --role "$LAMBDA_ROLE_ARN" \
        --handler lambda_function.lambda_handler \
        --zip-file "fileb://$BUILD_DIR/lambda.zip" \
        --timeout 60 --memory-size 512 \
        --environment "$ENV_VARS" >/dev/null 2>/tmp/lambda-create.err; then
      break
    fi
    if grep -q "cannot be assumed" /tmp/lambda-create.err; then
      echo "    waiting for IAM role propagation... ($i)"
      sleep 5
      continue
    fi
    cat /tmp/lambda-create.err >&2
    exit 1
  done
fi

LAMBDA_ARN="$(aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" \
  --query 'Configuration.FunctionArn' --output text)"

# 5. Allow S3 to invoke --------------------------------------------------------
aws lambda remove-permission --function-name "$LAMBDA_NAME" --region "$REGION" \
  --statement-id s3invoke >/dev/null 2>&1 || true
aws lambda add-permission --function-name "$LAMBDA_NAME" --region "$REGION" \
  --statement-id s3invoke --action lambda:InvokeFunction \
  --principal s3.amazonaws.com --source-arn "arn:aws:s3:::${BUCKET}" \
  --source-account "$ACCOUNT" >/dev/null

# 6. S3 notification on prompts/ ----------------------------------------------
NOTIF=$(cat <<JSON
{
  "LambdaFunctionConfigurations": [
    {
      "Id": "${PREFIX}-trigger",
      "LambdaFunctionArn": "${LAMBDA_ARN}",
      "Events": ["s3:ObjectCreated:*"],
      "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "prompts/"}]}}
    }
  ]
}
JSON
)
aws s3api put-bucket-notification-configuration --bucket "$BUCKET" --region "$REGION" \
  --notification-configuration "$NOTIF" >/dev/null

cat <<EOF

[step-function-generator] deployed
  Function:    $LAMBDA_NAME
  Bucket:      s3://$BUCKET
  Input pfx:   prompts/
  Output pfx:  generated/
  Model:       $MODEL_ID
  SFN role:    $SFN_ROLE_ARN

Try it:
  aws s3 cp sample-prompt.txt s3://$BUCKET/prompts/demo.txt --region $REGION
  aws logs tail /aws/lambda/$LAMBDA_NAME --region $REGION --follow
EOF
