#!/usr/bin/env bash
# Teardown for text-to-dag-agent.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-text-to-dag-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"

LAMBDA_NAME="${PREFIX}-tools"
LAMBDA_ROLE="${PREFIX}-lambda-role"
SFN_ROLE="${PREFIX}-sfn-role"
GW_ROLE="${PREFIX}-gateway-role"
RT_ROLE="${PREFIX}-runtime-role"
GW_NAME="${PREFIX}-gw"
USER_POOL_NAME="${PREFIX}-pool"
SECRET_NAME="${PREFIX}/gateway-credentials"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="text_to_dag_agent"

echo "[text-to-dag-agent] teardown region=$REGION account=$ACCOUNT"

# 1. Delete Step Functions state machines we created
for arn in $(aws stepfunctions list-state-machines --region "$REGION" \
              --query "stateMachines[?starts_with(name, \`${PREFIX}\`)].stateMachineArn" --output text 2>/dev/null); do
  [ -n "$arn" ] && [ "$arn" != "None" ] || continue
  echo "  delete state-machine $arn"
  aws stepfunctions delete-state-machine --state-machine-arn "$arn" --region "$REGION" >/dev/null || true
done

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

# 4. Lambda
if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  delete lambda $LAMBDA_NAME"
  aws lambda delete-function --function-name "$LAMBDA_NAME" --region "$REGION"
fi
for lg in "/aws/lambda/$LAMBDA_NAME"; do
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

# 6. Cognito (user pool + clients)
USER_POOL_ID="$(aws cognito-idp list-user-pools --max-results 50 --region "$REGION" \
                 --query "UserPools[?Name=='${USER_POOL_NAME}'].Id | [0]" --output text 2>/dev/null || true)"
if [ -n "$USER_POOL_ID" ] && [ "$USER_POOL_ID" != "None" ]; then
  DOMAIN="${PREFIX}-${ACCOUNT}"
  aws cognito-idp delete-user-pool-domain --user-pool-id "$USER_POOL_ID" --domain "$DOMAIN" --region "$REGION" >/dev/null 2>&1 || true
  echo "  delete user pool $USER_POOL_ID"
  aws cognito-idp delete-user-pool --user-pool-id "$USER_POOL_ID" --region "$REGION" || true
fi

# 7. Secret
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  delete secret $SECRET_NAME"
  aws secretsmanager delete-secret --secret-id "$SECRET_NAME" --region "$REGION" --force-delete-without-recovery >/dev/null || true
fi

# 8. IAM roles
for role in "$LAMBDA_ROLE" "$SFN_ROLE" "$GW_ROLE" "$RT_ROLE"; do
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

echo "[text-to-dag-agent] done"
