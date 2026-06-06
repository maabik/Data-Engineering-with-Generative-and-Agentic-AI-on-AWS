#!/bin/bash
# Provision (or reuse) a Cognito user pool for the Sales Reporting Agent
# and store its credentials (along with Redshift reader credentials) in
# Secrets Manager.
#
# Required env vars:
#   COGNITO_PASSWORD   — Cognito password for the agent user
#   REDSHIFT_PASSWORD  — Redshift sales_reader password
#
# Optional env vars:
#   COGNITO_USERNAME   — defaults to "salesagent"
#   REDSHIFT_USERNAME  — defaults to "sales_reader"
#   AWS_REGION         — defaults to "us-east-1"
#
# Idempotent: existing pool/client/secret are reused; the user's password
# and the secret value are refreshed.

set -euo pipefail

: "${COGNITO_PASSWORD:?Set COGNITO_PASSWORD before running this script}"
: "${REDSHIFT_PASSWORD:?Set REDSHIFT_PASSWORD before running this script}"
COGNITO_USERNAME="${COGNITO_USERNAME:-salesagent}"
REDSHIFT_USERNAME="${REDSHIFT_USERNAME:-sales_reader}"
REGION="${AWS_REGION:-us-east-1}"
POOL_NAME="SalesAgentUserPool"
CLIENT_NAME="SalesAgentClient"
SECRET_NAME="sales-agent-credentials"

echo "Setting up Cognito for Sales Reporting Agent..."

POOL_ID=$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" \
  | jq -r --arg n "$POOL_NAME" '.UserPools[] | select(.Name==$n) | .Id' | head -1)

if [ -z "$POOL_ID" ]; then
  POOL_ID=$(aws cognito-idp create-user-pool \
    --pool-name "$POOL_NAME" \
    --policies '{"PasswordPolicy":{"MinimumLength":8,"RequireUppercase":true,"RequireLowercase":true,"RequireNumbers":true}}' \
    --admin-create-user-config AllowAdminCreateUserOnly=true \
    --region "$REGION" | jq -r '.UserPool.Id')
  echo "Created user pool: $POOL_ID"
else
  echo "Reusing user pool: $POOL_ID"
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
  echo "Reusing client: $CLIENT_ID"
fi

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
echo "User $COGNITO_USERNAME ready"

SECRETS_JSON=$(jq -n \
  --arg pool_id "$POOL_ID" \
  --arg client_id "$CLIENT_ID" \
  --arg cognito_user "$COGNITO_USERNAME" \
  --arg cognito_pass "$COGNITO_PASSWORD" \
  --arg region "$REGION" \
  --arg rs_user "$REDSHIFT_USERNAME" \
  --arg rs_pass "$REDSHIFT_PASSWORD" \
  '{
    cognito: {
      user_pool_id: $pool_id,
      client_id: $client_id,
      username: $cognito_user,
      password: $cognito_pass,
      region: $region
    },
    redshift: {
      cluster_identifier: "sales-demo-cluster",
      database_name: "salesdb",
      username: $rs_user,
      password: $rs_pass
    }
  }')

if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" > /dev/null 2>&1; then
  aws secretsmanager put-secret-value \
    --secret-id "$SECRET_NAME" \
    --secret-string "$SECRETS_JSON" \
    --region "$REGION" > /dev/null
  echo "Updated secret: $SECRET_NAME"
else
  aws secretsmanager create-secret \
    --name "$SECRET_NAME" \
    --description "Credentials for Sales Reporting Agent" \
    --secret-string "$SECRETS_JSON" \
    --region "$REGION" > /dev/null
  echo "Created secret: $SECRET_NAME"
fi

echo ""
echo "Configuration:"
echo "  Pool ID:       $POOL_ID"
echo "  Client ID:     $CLIENT_ID"
echo "  Discovery URL: https://cognito-idp.$REGION.amazonaws.com/$POOL_ID/.well-known/openid-configuration"
echo "  Username:      $COGNITO_USERNAME"
echo "  Secret ID:     $SECRET_NAME"
