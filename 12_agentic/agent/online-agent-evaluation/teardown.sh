#!/usr/bin/env bash
# Online Agent Evaluation — single-shot teardown.
#
# Removes subscription filters from every agent log group first (so further
# invocations are not evaluated), then deletes the per-region Lambda + role
# + DynamoDB table. Verdicts buckets are kept by default (pass --purge-bucket
# to wipe them too).
set -euo pipefail

PRIMARY_REGION="${PRIMARY_REGION:-us-east-1}"
SECONDARY_REGION="${SECONDARY_REGION:-us-west-2}"
PREFIX="${PREFIX:-oae}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
PURGE_BUCKET=0

for arg in "$@"; do
  [ "$arg" = "--purge-bucket" ] && PURGE_BUCKET=1
done

LAMBDA_NAME="${PREFIX}-evaluator"
ROLE_NAME="${PREFIX}-evaluator-role"
RATES_TABLE="${PREFIX}-rates"
SUB_FILTER_NAME="${PREFIX}-sub"

echo "[online-agent-evaluation] teardown account=$ACCOUNT"

remove_subscriptions() {
  local region="$1"
  local lambda_arn="arn:aws:lambda:${region}:${ACCOUNT}:function:${LAMBDA_NAME}"
  echo "  [$region] remove subscription filters pointing at $LAMBDA_NAME"
  # Find every log group that has our subscription filter; describe-subscription-filters
  # is per-log-group, so iterate via tag-based discovery: list-log-groups paginated.
  aws logs describe-log-groups --region "$region" --query 'logGroups[].logGroupName' \
    --output text | tr '\t' '\n' | while read -r lg; do
    [ -z "$lg" ] && continue
    has=$(aws logs describe-subscription-filters --region "$region" --log-group-name "$lg" \
      --query "subscriptionFilters[?filterName=='${SUB_FILTER_NAME}' && destinationArn=='${lambda_arn}'].filterName" \
      --output text 2>/dev/null || true)
    if [ "$has" = "$SUB_FILTER_NAME" ]; then
      echo "    detach $lg"
      aws logs delete-subscription-filter --region "$region" \
        --log-group-name "$lg" --filter-name "$SUB_FILTER_NAME" >/dev/null 2>&1 || true
    fi
  done
}

remove_subscriptions "$PRIMARY_REGION"
remove_subscriptions "$SECONDARY_REGION"

remove_lambda() {
  local region="$1"
  if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$region" >/dev/null 2>&1; then
    echo "  [$region] delete lambda $LAMBDA_NAME"
    aws lambda delete-function --function-name "$LAMBDA_NAME" --region "$region" >/dev/null
  fi
}
remove_lambda "$PRIMARY_REGION"
remove_lambda "$SECONDARY_REGION"

if aws dynamodb describe-table --table-name "$RATES_TABLE" --region "$PRIMARY_REGION" >/dev/null 2>&1; then
  echo "  delete rates table"
  aws dynamodb delete-table --table-name "$RATES_TABLE" --region "$PRIMARY_REGION" >/dev/null
fi

if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "  delete role $ROLE_NAME"
  aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name inline >/dev/null 2>&1 || true
  aws iam delete-role --role-name "$ROLE_NAME" >/dev/null
fi

for region in "$PRIMARY_REGION" "$SECONDARY_REGION"; do
  bucket="${PREFIX}-${ACCOUNT}-${region}"
  if [ "$PURGE_BUCKET" = "1" ] && aws s3api head-bucket --bucket "$bucket" --region "$region" >/dev/null 2>&1; then
    echo "  purge bucket $bucket"
    aws s3api delete-objects --bucket "$bucket" --region "$region" --delete "$(aws s3api list-object-versions --bucket "$bucket" --region "$region" --output json --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}')" >/dev/null 2>&1 || true
    aws s3api delete-objects --bucket "$bucket" --region "$region" --delete "$(aws s3api list-object-versions --bucket "$bucket" --region "$region" --output json --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}')" >/dev/null 2>&1 || true
    aws s3api delete-bucket --bucket "$bucket" --region "$region" >/dev/null 2>&1 || true
  fi
done

aws ssm delete-parameter --region "$PRIMARY_REGION" --name "/${PREFIX}/lambda_arn"      >/dev/null 2>&1 || true
aws ssm delete-parameter --region "$PRIMARY_REGION" --name "/${PREFIX}/verdicts_bucket" >/dev/null 2>&1 || true
aws ssm delete-parameter --region "$PRIMARY_REGION" --name "/${PREFIX}/rates_table"     >/dev/null 2>&1 || true

echo "✅ teardown complete"
