#!/bin/bash
# Provision (or reuse) a Cognito user pool for the Redshift MCP server.
#
# Required env vars:
#   COGNITO_PASSWORD   — permanent password for the pool user (min 8 chars)
#
# Optional env vars:
#   COGNITO_USERNAME   — defaults to "redshiftuser"
#   AWS_REGION         — defaults to "us-east-1"
#
# Idempotent: if a pool with the same name already exists, it is reused
# (and the user's password is reset to COGNITO_PASSWORD). The bearer
# token is not echoed — fetch it on demand with `aws cognito-idp
# initiate-auth` after the pool exists.

set -euo pipefail

: "${COGNITO_PASSWORD:?Set COGNITO_PASSWORD before running this script}"
COGNITO_USERNAME="${COGNITO_USERNAME:-redshiftuser}"
REGION="${AWS_REGION:-us-east-1}"
POOL_NAME="RedshiftMCPUserPool"
CLIENT_NAME="RedshiftMCPClient"

POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" \
  | jq -r --arg n "$POOL_NAME" '.UserPools[] | select(.Name==$n) | .Id' | head -1)

if [ -z "$POOL_ID" ]; then
  POOL_ID=$(aws cognito-idp create-user-pool \
    --pool-name "$POOL_NAME" \
    --policies '{"PasswordPolicy":{"MinimumLength":8}}' \
    --admin-create-user-config AllowAdminCreateUserOnly=true \
    --region "$REGION" | jq -r '.UserPool.Id')
  echo "Created user pool: $POOL_ID"
else
  echo "Reusing existing user pool: $POOL_ID"
fi

CLIENT_ID=$(aws cognito-idp list-user-pool-clients --user-pool-id "$POOL_ID" --region "$REGION" \
  | jq -r --arg n "$CLIENT_NAME" '.UserPoolClients[] | select(.ClientName==$n) | .ClientId' | head -1)

if [ -z "$CLIENT_ID" ]; then
  CLIENT_ID=$(aws cognito-idp create-user-pool-client \
    --user-pool-id "$POOL_ID" \
    --client-name "$CLIENT_NAME" \
    --no-generate-secret \
    --explicit-auth-flows "ALLOW_USER_PASSWORD_AUTH" "ALLOW_REFRESH_TOKEN_AUTH" \
    --region "$REGION" | jq -r '.UserPoolClient.ClientId')
  echo "Created client: $CLIENT_ID"
else
  echo "Reusing existing client: $CLIENT_ID"
fi

# Create-or-no-op the user, then set the permanent password unconditionally.
aws cognito-idp admin-create-user \
  --user-pool-id "$POOL_ID" \
  --username "$COGNITO_USERNAME" \
  --temporary-password "$COGNITO_PASSWORD" \
  --region "$REGION" \
  --message-action SUPPRESS > /dev/null 2>&1 || true

aws cognito-idp admin-set-user-password \
  --user-pool-id "$POOL_ID" \
  --username "$COGNITO_USERNAME" \
  --password "$COGNITO_PASSWORD" \
  --region "$REGION" \
  --permanent > /dev/null

echo "Pool ID:        $POOL_ID"
echo "Client ID:      $CLIENT_ID"
echo "Discovery URL:  https://cognito-idp.$REGION.amazonaws.com/$POOL_ID/.well-known/openid-configuration"
echo "Username:       $COGNITO_USERNAME"
