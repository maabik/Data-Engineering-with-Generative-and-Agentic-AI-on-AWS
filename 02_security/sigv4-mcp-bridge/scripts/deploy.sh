#!/usr/bin/env bash
# Package the Lambda, upload to a staging bucket, deploy the CFN stack,
# and write outputs to .env.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_NAME="${STACK_NAME:-sigv4-mcp-bridge}"
STACK_PREFIX="${STACK_PREFIX:-sigv4-mcp-bridge}"
REGION="${AWS_REGION:-$(aws configure get region)}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${LAMBDA_BUCKET:-${STACK_PREFIX}-lambda-${ACCOUNT_ID}-${REGION}}"
KEY="mcp_app.zip"

if ! aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
  echo "Creating staging bucket $BUCKET"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
  fi
  aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
fi

TMP=$(mktemp -d)
cp "$HERE/lambda-mcp/mcp_app.py" "$TMP/"
( cd "$TMP" && zip -q mcp_app.zip mcp_app.py )
aws s3 cp "$TMP/mcp_app.zip" "s3://$BUCKET/$KEY" --region "$REGION"
rm -rf "$TMP"

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file "$HERE/infra/bridge.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    StackPrefix="$STACK_PREFIX" \
    LambdaCodeBucket="$BUCKET" \
    LambdaCodeKey="$KEY" \
  --region "$REGION"

# Force Lambda to pull the fresh zip even if CFN sees no config change.
aws lambda update-function-code \
  --function-name "${STACK_PREFIX}-mcp" \
  --s3-bucket "$BUCKET" \
  --s3-key "$KEY" \
  --region "$REGION" >/dev/null

outputs=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'Stacks[0].Outputs' --output json)
get() { echo "$outputs" | python3 -c "import json,sys;print([o['OutputValue'] for o in json.load(sys.stdin) if o['OutputKey']=='$1'][0])"; }

FUNCTION_URL=$(get FunctionUrl)
CALLER_ROLE_ARN=$(get CallerRoleArn)

cat > "$HERE/.env" <<EOF
export AWS_REGION=$REGION
export MCP_REGION=$REGION
export MCP_FUNCTION_URL=${FUNCTION_URL%/}
export MCP_CALLER_ROLE_ARN=$CALLER_ROLE_ARN
export LAMBDA_BUCKET=$BUCKET
EOF

echo "Wrote $HERE/.env — run \`source .env\` before invoking the agent."
