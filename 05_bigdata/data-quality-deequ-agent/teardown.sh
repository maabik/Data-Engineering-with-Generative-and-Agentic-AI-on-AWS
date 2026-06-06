#!/usr/bin/env bash
# Teardown for data-quality-deequ-agent. Drops only this agent's resources;
# leaves the shared data bucket and the data-architect-agent-owned databases in place
# unless DELETE_DATA_QUALITY_DEEQU_AGENT_DATA=1, in which case dq-reports/,
# dq-insights/, athena-results/, and the Deequ Glue script are emptied too.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-data-quality-deequ-agent}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
DATA_BUCKET="${DATA_BUCKET:-data-profiling-agent-${ACCOUNT}-${REGION}}"
DQ_DATABASE="${DQ_DATABASE:-data_quality_deequ_agent_dq}"

LAMBDA_NAME="${PREFIX}-tools"
BRIDGE_NAME="${PREFIX}-bridge"
LAMBDA_ROLE="${PREFIX}-lambda-role"
BRIDGE_ROLE="${PREFIX}-bridge-role"
GLUE_ROLE="${PREFIX}-glue-role"
GW_ROLE="${PREFIX}-gateway-role"
RT_ROLE="${PREFIX}-runtime-role"
GW_NAME="${PREFIX}-gw"
USER_POOL_NAME="${PREFIX}-pool"
SECRET_NAME="${PREFIX}/gateway-credentials"
ECR_REPO="${PREFIX}"
RUNTIME_NAME="data_quality_deequ_agent"
EB_RULE_NAME="${PREFIX}-designs-rule"
DEEQU_JOB_NAME="${PREFIX}-deequ-job"
ATHENA_WORKGROUP="${PREFIX}-wg"

echo "[data-quality-deequ-agent] teardown region=$REGION account=$ACCOUNT"

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

# 6. Glue Deequ job and tables in this agent's DQ database
if aws glue get-job --job-name "$DEEQU_JOB_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  delete glue job $DEEQU_JOB_NAME"
  aws glue delete-job --job-name "$DEEQU_JOB_NAME" --region "$REGION" >/dev/null || true
fi
if aws glue get-database --name "$DQ_DATABASE" --region "$REGION" >/dev/null 2>&1; then
  for t in $(aws glue get-tables --database-name "$DQ_DATABASE" --region "$REGION" \
              --query 'TableList[].Name' --output text 2>/dev/null); do
    [ -n "$t" ] && [ "$t" != "None" ] || continue
    echo "  delete glue table $DQ_DATABASE.$t"
    aws glue delete-table --database-name "$DQ_DATABASE" --name "$t" --region "$REGION" >/dev/null || true
  done
  echo "  delete glue database $DQ_DATABASE"
  aws glue delete-database --name "$DQ_DATABASE" --region "$REGION" >/dev/null || true
fi

# 7. Athena workgroup
if aws athena get-work-group --work-group "$ATHENA_WORKGROUP" --region "$REGION" >/dev/null 2>&1; then
  echo "  delete athena workgroup $ATHENA_WORKGROUP"
  aws athena delete-work-group --work-group "$ATHENA_WORKGROUP" --region "$REGION" --recursive-delete-option >/dev/null || true
fi

# 8. (Optional) clean data-quality-deequ-agent-owned prefixes from the shared bucket
if [ "${DELETE_DATA_QUALITY_DEEQU_AGENT_DATA:-0}" = "1" ] && aws s3api head-bucket --bucket "$DATA_BUCKET" --region "$REGION" >/dev/null 2>&1; then
  for p in dq-reports/ dq-insights/ athena-results/ glue-scripts/deequ_job.py; do
    echo "  empty s3://$DATA_BUCKET/$p"
    aws s3 rm "s3://$DATA_BUCKET/$p" --recursive --region "$REGION" >/dev/null 2>&1 \
      || aws s3 rm "s3://$DATA_BUCKET/$p" --region "$REGION" >/dev/null 2>&1 || true
  done
fi

# 9. Cognito user pool
USER_POOL_ID="$(aws cognito-idp list-user-pools --max-results 50 --region "$REGION" \
                 --query "UserPools[?Name=='${USER_POOL_NAME}'].Id | [0]" --output text 2>/dev/null || true)"
if [ -n "$USER_POOL_ID" ] && [ "$USER_POOL_ID" != "None" ]; then
  DOMAIN="${PREFIX}-${ACCOUNT}"
  aws cognito-idp delete-user-pool-domain --user-pool-id "$USER_POOL_ID" --domain "$DOMAIN" --region "$REGION" >/dev/null 2>&1 || true
  echo "  delete user pool $USER_POOL_ID"
  aws cognito-idp delete-user-pool --user-pool-id "$USER_POOL_ID" --region "$REGION" || true
fi

# 10. Secret
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  delete secret $SECRET_NAME"
  aws secretsmanager delete-secret --secret-id "$SECRET_NAME" --region "$REGION" --force-delete-without-recovery >/dev/null || true
fi

# 11. IAM roles
for role in "$LAMBDA_ROLE" "$BRIDGE_ROLE" "$GLUE_ROLE" "$GW_ROLE" "$RT_ROLE"; do
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

echo "[data-quality-deequ-agent] done"
