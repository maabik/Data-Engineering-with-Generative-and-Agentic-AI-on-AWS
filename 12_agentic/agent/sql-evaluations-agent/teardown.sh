#!/usr/bin/env bash
# Idempotent teardown for sql-evaluations-agent.
# Deletes (in order): runtime, gateway target, gateway, lambda, ECR repo,
# secrets, cognito user pool, IAM roles, SSM params, S3 bucket (last).
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-sql-evaluations-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

STEERING_BUCKET="${STEERING_BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"
LAMBDA_NAME="${PREFIX}-tools"
GW_NAME="${PREFIX}-gw"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="sql_evaluations_agent"
USER_POOL_NAME="${PREFIX}-pool"
SECRET_NAME="${PREFIX}/gateway-credentials"

echo "[sql-evaluations-agent] teardown region=$REGION"

# 1. AgentCore Runtime
RT_ID="$(aws bedrock-agentcore-control list-agent-runtimes --no-paginate --region "$REGION" --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" --output text 2>/dev/null || true)"
if [ -n "$RT_ID" ] && [ "$RT_ID" != "None" ]; then
  echo "  delete runtime $RUNTIME_NAME ($RT_ID)"
  aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RT_ID" --region "$REGION" >/dev/null || true
fi

# 2. Gateway target + gateway
GW_ID="$(aws bedrock-agentcore-control list-gateways --region "$REGION" --query "items[?name=='${GW_NAME}'].gatewayId | [0]" --output text 2>/dev/null || true)"
if [ -n "$GW_ID" ] && [ "$GW_ID" != "None" ]; then
  for tid in $(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --region "$REGION" --query 'items[].targetId' --output text 2>/dev/null || true); do
    [ -n "$tid" ] && [ "$tid" != "None" ] && \
      aws bedrock-agentcore-control delete-gateway-target --gateway-identifier "$GW_ID" --target-id "$tid" --region "$REGION" >/dev/null || true
  done
  echo "  delete gateway $GW_NAME ($GW_ID)"
  aws bedrock-agentcore-control delete-gateway --gateway-identifier "$GW_ID" --region "$REGION" >/dev/null || true
fi

# 3. Lambda
aws lambda delete-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null 2>&1 || true

# 4. ECR
aws ecr delete-repository --repository-name "$ECR_REPO" --force --region "$REGION" >/dev/null 2>&1 || true

# 5. Secrets
aws secretsmanager delete-secret --secret-id "$SECRET_NAME" --force-delete-without-recovery --region "$REGION" >/dev/null 2>&1 || true

# 6. Cognito user pool
USER_POOL_ID="$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" --query "UserPools[?Name=='${USER_POOL_NAME}'].Id | [0]" --output text 2>/dev/null || true)"
if [ -n "$USER_POOL_ID" ] && [ "$USER_POOL_ID" != "None" ]; then
  echo "  delete user pool $USER_POOL_ID"
  aws cognito-idp delete-user-pool --user-pool-id "$USER_POOL_ID" --region "$REGION" >/dev/null || true
fi

# 7. IAM roles
for role in "${PREFIX}-runtime-role" "${PREFIX}-lambda-role" "${PREFIX}-gateway-role"; do
  if aws iam get-role --role-name "$role" >/dev/null 2>&1; then
    for p in $(aws iam list-role-policies --role-name "$role" --query 'PolicyNames[]' --output text 2>/dev/null || true); do
      aws iam delete-role-policy --role-name "$role" --policy-name "$p" >/dev/null || true
    done
    aws iam delete-role --role-name "$role" >/dev/null || true
    echo "  deleted role $role"
  fi
done

# 8. SSM parameters
aws ssm delete-parameters --names /sql-evaluations-agent/agent_runtime_arn /sql-evaluations-agent/gateway_url --region "$REGION" >/dev/null 2>&1 || true

# 9. S3 bucket (only if it's empty after we wipe it)
if aws s3api head-bucket --bucket "$STEERING_BUCKET" --region "$REGION" >/dev/null 2>&1; then
  echo "  empty + delete bucket $STEERING_BUCKET"
  aws s3 rm "s3://$STEERING_BUCKET" --recursive --region "$REGION" >/dev/null 2>&1 || true
  # also remove versioned objects
  aws s3api list-object-versions --bucket "$STEERING_BUCKET" --region "$REGION" --output json 2>/dev/null \
    | jq -c '(.Versions // []) + (.DeleteMarkers // []) | map({Key, VersionId})' \
    | jq -c '. as $a | range(0; ($a | length); 900) as $i | {Objects: ($a[$i:$i+900]), Quiet: true}' 2>/dev/null \
    | while read -r batch; do
        [ "$batch" = "{\"Objects\":[],\"Quiet\":true}" ] && continue
        aws s3api delete-objects --bucket "$STEERING_BUCKET" --region "$REGION" --delete "$batch" >/dev/null 2>&1 || true
      done
  aws s3api delete-bucket --bucket "$STEERING_BUCKET" --region "$REGION" >/dev/null 2>&1 || true
fi

echo "✅ teardown complete"
