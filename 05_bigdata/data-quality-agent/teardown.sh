#!/usr/bin/env bash
# Teardown for data-quality-agent. Drops only data-quality-agent-owned resources; leaves
# the shared data bucket, the data-quality-deequ-agent-owned Athena workgroup, and the data-quality-deequ-agent / data-architect-agent
# Glue databases in place. With DELETE_DQR_DATA=1 the dq-remediation/
# prefix is also emptied.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-data-quality-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
DATA_BUCKET="${DATA_BUCKET:-data-profiling-agent-${ACCOUNT}-${REGION}}"

LAMBDA_NAME="${PREFIX}-tools"
BRIDGE_NAME="${PREFIX}-bridge"
LAMBDA_ROLE="${PREFIX}-lambda-role"
BRIDGE_ROLE="${PREFIX}-bridge-role"
GW_ROLE="${PREFIX}-gateway-role"
RT_ROLE="${PREFIX}-runtime-role"
GW_NAME="${PREFIX}-gw"
USER_POOL_NAME="${PREFIX}-pool"
SECRET_NAME="${PREFIX}/gateway-credentials"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="data_quality_agent"
EB_RULE_NAME="${PREFIX}-insights-rule"

echo "[data-quality-agent] teardown region=$REGION account=$ACCOUNT"

# 1. EventBridge rule + targets
if aws events describe-rule --name "$EB_RULE_NAME" --region "$REGION" >/dev/null 2>&1; then
  for tid in $(aws events list-targets-by-rule --rule "$EB_RULE_NAME" --region "$REGION" \
                --query 'Targets[].Id' --output text 2>/dev/null); do
    [ -n "$tid" ] && [ "$tid" != "None" ] || continue
    aws events remove-targets --rule "$EB_RULE_NAME" --region "$REGION" --ids "$tid" >/dev/null || true
  done
  echo "  delete eventbridge rule $EB_RULE_NAME"
  aws events delete-rule --name "$EB_RULE_NAME" --region "$REGION" || true
fi

# 2. AgentCore Runtime
RT_ID="$(aws bedrock-agentcore-control list-agent-runtimes --no-paginate --region "$REGION" \
          --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" --output text 2>/dev/null || true)"
if [ -n "$RT_ID" ] && [ "$RT_ID" != "None" ]; then
  echo "  delete agent runtime $RUNTIME_NAME"
  aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RT_ID" --region "$REGION" >/dev/null || true
fi

# 3. AgentCore Gateway + targets
GW_ID="$(aws bedrock-agentcore-control list-gateways --region "$REGION" \
          --query "items[?name=='${GW_NAME}'].gatewayId | [0]" --output text 2>/dev/null || true)"
if [ -n "$GW_ID" ] && [ "$GW_ID" != "None" ]; then
  for tid in $(aws bedrock-agentcore-control list-gateway-targets --gateway-identifier "$GW_ID" --region "$REGION" \
                --query 'items[].targetId' --output text 2>/dev/null); do
    [ -n "$tid" ] && [ "$tid" != "None" ] || continue
    echo "  delete gateway target $tid"
    aws bedrock-agentcore-control delete-gateway-target --gateway-identifier "$GW_ID" --target-id "$tid" --region "$REGION" >/dev/null || true
  done
  echo "  delete gateway $GW_NAME"
  aws bedrock-agentcore-control delete-gateway --gateway-identifier "$GW_ID" --region "$REGION" >/dev/null || true
fi

# 4. Lambdas + log groups
for fn in "$BRIDGE_NAME" "$LAMBDA_NAME"; do
  if aws lambda get-function --function-name "$fn" --region "$REGION" >/dev/null 2>&1; then
    echo "  delete lambda $fn"
    aws lambda delete-function --function-name "$fn" --region "$REGION" || true
  fi
  lg="/aws/lambda/$fn"
  if aws logs describe-log-groups --log-group-name-prefix "$lg" --region "$REGION" \
       --query 'logGroups[0].logGroupName' --output text 2>/dev/null | grep -q "$lg"; then
    aws logs delete-log-group --log-group-name "$lg" --region "$REGION" || true
  fi
done

# 5. ECR repo
if aws ecr describe-repositories --repository-names "$ECR_REPO" --region "$REGION" >/dev/null 2>&1; then
  echo "  delete ecr repo $ECR_REPO"
  aws ecr delete-repository --repository-name "$ECR_REPO" --region "$REGION" --force >/dev/null || true
fi

# 6. (Optional) clean data-quality-agent-owned prefixes from the shared bucket
if [ "${DELETE_DQR_DATA:-0}" = "1" ] && aws s3api head-bucket --bucket "$DATA_BUCKET" --region "$REGION" >/dev/null 2>&1; then
  for p in dq-remediation/; do
    echo "  empty s3://$DATA_BUCKET/$p"
    aws s3 rm "s3://$DATA_BUCKET/$p" --recursive --region "$REGION" >/dev/null 2>&1 || true
  done
fi

# 7. Cognito user pool
USER_POOL_ID="$(aws cognito-idp list-user-pools --max-results 50 --region "$REGION" \
                 --query "UserPools[?Name=='${USER_POOL_NAME}'].Id | [0]" --output text 2>/dev/null || true)"
if [ -n "$USER_POOL_ID" ] && [ "$USER_POOL_ID" != "None" ]; then
  DOMAIN="${PREFIX}-${ACCOUNT}"
  aws cognito-idp delete-user-pool-domain --user-pool-id "$USER_POOL_ID" --domain "$DOMAIN" --region "$REGION" >/dev/null 2>&1 || true
  echo "  delete user pool $USER_POOL_ID"
  aws cognito-idp delete-user-pool --user-pool-id "$USER_POOL_ID" --region "$REGION" || true
fi

# 8. Secret
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  delete secret $SECRET_NAME"
  aws secretsmanager delete-secret --secret-id "$SECRET_NAME" --region "$REGION" --force-delete-without-recovery >/dev/null || true
fi

# 9. Lake Formation grants on data_quality_deequ_agent_dq + data_architect_agent_curated to data-quality-agent-lambda-role
LAMBDA_ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${LAMBDA_ROLE}"
for db in data_quality_deequ_agent_dq data_architect_agent_curated; do
  if aws glue get-database --name "$db" --region "$REGION" >/dev/null 2>&1; then
    aws lakeformation revoke-permissions --region "$REGION" \
      --principal "DataLakePrincipalIdentifier=$LAMBDA_ROLE_ARN" \
      --resource "{\"Table\":{\"DatabaseName\":\"$db\",\"TableWildcard\":{}}}" \
      --permissions SELECT DESCRIBE >/dev/null 2>&1 || true
    aws lakeformation revoke-permissions --region "$REGION" \
      --principal "DataLakePrincipalIdentifier=$LAMBDA_ROLE_ARN" \
      --resource "{\"Database\":{\"Name\":\"$db\"}}" \
      --permissions DESCRIBE >/dev/null 2>&1 || true
  fi
done

# 10. IAM roles
for role in "$LAMBDA_ROLE" "$BRIDGE_ROLE" "$GW_ROLE" "$RT_ROLE"; do
  if aws iam get-role --role-name "$role" >/dev/null 2>&1; then
    for p in $(aws iam list-role-policies --role-name "$role" --query 'PolicyNames' --output text); do
      aws iam delete-role-policy --role-name "$role" --policy-name "$p" || true
    done
    for p in $(aws iam list-attached-role-policies --role-name "$role" --query 'AttachedPolicies[].PolicyArn' --output text); do
      aws iam detach-role-policy --role-name "$role" --policy-arn "$p" || true
    done
    echo "  delete role $role"
    aws iam delete-role --role-name "$role" || true
  fi
done

echo "[data-quality-agent] done"
