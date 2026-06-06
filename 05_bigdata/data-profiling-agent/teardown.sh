#!/usr/bin/env bash
# Teardown for data-profiling-agent.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-data-profiling-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"

LAMBDA_NAME="${PREFIX}-tools"
BRIDGE_NAME="${PREFIX}-bridge"
LAMBDA_ROLE="${PREFIX}-lambda-role"
BRIDGE_ROLE="${PREFIX}-bridge-role"
DATABREW_ROLE="${PREFIX}-databrew-role"
GW_ROLE="${PREFIX}-gateway-role"
RT_ROLE="${PREFIX}-runtime-role"
GW_NAME="${PREFIX}-gw"
USER_POOL_NAME="${PREFIX}-pool"
SECRET_NAME="${PREFIX}/gateway-credentials"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="data_profiling_agent"
EB_RULE_NAME="${PREFIX}-landing-rule"

echo "[data-profiling-agent] teardown region=$REGION account=$ACCOUNT bucket=$BUCKET"

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

# 6. DataBrew profile jobs + datasets created by the tool
for j in $(aws databrew list-jobs --region "$REGION" --query "Jobs[?starts_with(Name, \`${PREFIX}-\`)].Name" --output text 2>/dev/null); do
  [ -n "$j" ] && [ "$j" != "None" ] || continue
  for run in $(aws databrew list-job-runs --name "$j" --region "$REGION" \
                --query 'JobRuns[?State==`RUNNING` || State==`STARTING`].RunId' --output text 2>/dev/null); do
    [ -n "$run" ] && [ "$run" != "None" ] || continue
    aws databrew stop-job-run --name "$j" --run-id "$run" --region "$REGION" >/dev/null || true
  done
  echo "  delete databrew job $j"
  aws databrew delete-job --name "$j" --region "$REGION" >/dev/null || true
done
for d in $(aws databrew list-datasets --region "$REGION" --query "Datasets[?starts_with(Name, \`${PREFIX}-\`)].Name" --output text 2>/dev/null); do
  [ -n "$d" ] && [ "$d" != "None" ] || continue
  echo "  delete databrew dataset $d"
  aws databrew delete-dataset --name "$d" --region "$REGION" >/dev/null || true
done

# 7. S3 bucket (empty + delete)
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null 2>&1; then
  echo "  empty s3://$BUCKET"
  aws s3 rm "s3://$BUCKET" --recursive --region "$REGION" >/dev/null || true
  echo "  delete bucket $BUCKET"
  aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION" || true
fi

# 8. Cognito user pool
USER_POOL_ID="$(aws cognito-idp list-user-pools --max-results 50 --region "$REGION" \
                 --query "UserPools[?Name=='${USER_POOL_NAME}'].Id | [0]" --output text 2>/dev/null || true)"
if [ -n "$USER_POOL_ID" ] && [ "$USER_POOL_ID" != "None" ]; then
  DOMAIN="${PREFIX}-${ACCOUNT}"
  aws cognito-idp delete-user-pool-domain --user-pool-id "$USER_POOL_ID" --domain "$DOMAIN" --region "$REGION" >/dev/null 2>&1 || true
  echo "  delete user pool $USER_POOL_ID"
  aws cognito-idp delete-user-pool --user-pool-id "$USER_POOL_ID" --region "$REGION" || true
fi

# 9. Secret
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  delete secret $SECRET_NAME"
  aws secretsmanager delete-secret --secret-id "$SECRET_NAME" --region "$REGION" --force-delete-without-recovery >/dev/null || true
fi

# 10. IAM roles
for role in "$LAMBDA_ROLE" "$BRIDGE_ROLE" "$DATABREW_ROLE" "$GW_ROLE" "$RT_ROLE"; do
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

echo "[data-profiling-agent] done"
