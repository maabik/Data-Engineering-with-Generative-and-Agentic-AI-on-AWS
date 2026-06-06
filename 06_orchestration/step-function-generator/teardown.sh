#!/usr/bin/env bash
# Teardown for step-function-generator.
#
# Deletes:
#   - All state machines whose name starts with ${PREFIX}
#   - S3 bucket notification + bucket contents + bucket
#   - Lambda function ${PREFIX}-fn
#   - IAM roles ${PREFIX}-lambda-role, ${PREFIX}-sfn-role
#   - CloudWatch log group /aws/lambda/${PREFIX}-fn
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-sfn-gen}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"

LAMBDA_NAME="${PREFIX}-fn"
LAMBDA_ROLE="${PREFIX}-lambda-role"
SFN_ROLE="${PREFIX}-sfn-role"
LOG_GROUP="/aws/lambda/${LAMBDA_NAME}"

echo "[step-function-generator] teardown region=$REGION account=$ACCOUNT"

# 1. State machines
for arn in $(aws stepfunctions list-state-machines --region "$REGION" \
              --query "stateMachines[?starts_with(name, \`${PREFIX}\`)].stateMachineArn" \
              --output text 2>/dev/null); do
  [ -n "$arn" ] && [ "$arn" != "None" ] || continue
  echo "  delete state-machine $arn"
  aws stepfunctions delete-state-machine --state-machine-arn "$arn" --region "$REGION" >/dev/null || true
done

# 2. S3 notification + contents + bucket
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
  echo "  clear bucket notifications"
  aws s3api put-bucket-notification-configuration --bucket "$BUCKET" --region "$REGION" \
    --notification-configuration '{}' >/dev/null || true
  echo "  empty s3://$BUCKET"
  aws s3 rm "s3://$BUCKET" --recursive >/dev/null || true
  echo "  delete bucket $BUCKET"
  aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null || true
fi

# 3. Lambda
if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  delete lambda $LAMBDA_NAME"
  aws lambda delete-function --function-name "$LAMBDA_NAME" --region "$REGION"
fi

# 4. CloudWatch log group
if aws logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" --region "$REGION" \
     --query 'logGroups[0].logGroupName' --output text 2>/dev/null | grep -q "$LOG_GROUP"; then
  echo "  delete log group $LOG_GROUP"
  aws logs delete-log-group --log-group-name "$LOG_GROUP" --region "$REGION" || true
fi

# 5. IAM roles
for role in "$LAMBDA_ROLE" "$SFN_ROLE"; do
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

echo "[step-function-generator] done"
