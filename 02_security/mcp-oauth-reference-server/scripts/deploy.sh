#!/usr/bin/env bash
# Deploy the Cognito stack and write a .env file the other components source.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_NAME="${STACK_NAME:-mcp-oauth-reference}"
STACK_PREFIX="${STACK_PREFIX:-mcp-oauth-ref}"
DOMAIN_PREFIX="${DOMAIN_PREFIX:-mcp-oauth-ref-$(LC_ALL=C head -c 32 /dev/urandom | LC_ALL=C tr -dc 'a-z0-9' | cut -c1-8)}"
CALLBACK_URL="${CALLBACK_URL:-http://localhost:8765/callback}"
REGION="${AWS_REGION:-$(aws configure get region)}"

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file "$HERE/infra/cognito.yaml" \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
    StackPrefix="$STACK_PREFIX" \
    DomainPrefix="$DOMAIN_PREFIX" \
    CallbackUrl="$CALLBACK_URL" \
  --region "$REGION"

outputs=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query 'Stacks[0].Outputs' \
  --output json)

get() { echo "$outputs" | python3 -c "import json,sys;print([o['OutputValue'] for o in json.load(sys.stdin) if o['OutputKey']=='$1'][0])"; }

USER_POOL_ID=$(get UserPoolId)
ISSUER=$(get Issuer)
HOSTED=$(get HostedUiDomain)
RESOURCE_SERVER=$(get ResourceServerIdentifier)

cat > "$HERE/.env" <<EOF
export AWS_REGION=$REGION
export COGNITO_USER_POOL_ID=$USER_POOL_ID
export COGNITO_ISSUER=$ISSUER
export COGNITO_HOSTED_DOMAIN=$HOSTED
export MCP_RESOURCE_SERVER_IDENTIFIER=$RESOURCE_SERVER
export MCP_PROTECTED_RESOURCE_METADATA_URL=http://localhost:8080/.well-known/oauth-protected-resource
export CLIENT_REDIRECT_URI=$CALLBACK_URL
EOF

echo "Wrote $HERE/.env — run \`source .env\` before starting the services."
