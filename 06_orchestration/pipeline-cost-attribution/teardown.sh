#!/usr/bin/env bash
# Teardown for pipeline-cost-attribution.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-pipeline-cost}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"
GLUE_DB="${GLUE_DB:-pipeline_cost_attribution}"

LAMBDA_ROLE="${PREFIX}-lambda-role"
TAG_FN="${PREFIX}-tag-enforcer"
AGG_FN="${PREFIX}-cost-aggregator"
WG_NAME="${PREFIX}-wg"
EVT_RULE="${PREFIX}-create-events"
SCHED_RULE="${PREFIX}-daily"

echo "[pipeline-cost-attribution] teardown region=$REGION account=$ACCOUNT"

# 1. EventBridge rules + targets
for rule in "$EVT_RULE" "$SCHED_RULE"; do
  if aws events describe-rule --name "$rule" --region "$REGION" >/dev/null 2>&1; then
    ids="$(aws events list-targets-by-rule --rule "$rule" --region "$REGION" --query 'Targets[].Id' --output text)"
    [ -n "$ids" ] && aws events remove-targets --rule "$rule" --region "$REGION" --ids $ids >/dev/null || true
    echo "  delete rule $rule"
    aws events delete-rule --name "$rule" --region "$REGION" || true
  fi
done

# 2. Lambdas
for fn in "$TAG_FN" "$AGG_FN"; do
  if aws lambda get-function --function-name "$fn" --region "$REGION" >/dev/null 2>&1; then
    echo "  delete lambda $fn"
    aws lambda delete-function --function-name "$fn" --region "$REGION"
  fi
  for lg in "/aws/lambda/$fn"; do
    if aws logs describe-log-groups --log-group-name-prefix "$lg" --region "$REGION" \
         --query 'logGroups[0].logGroupName' --output text 2>/dev/null | grep -q "$lg"; then
      aws logs delete-log-group --log-group-name "$lg" --region "$REGION" || true
    fi
  done
done

# 3. Athena workgroup (with recursive flag to remove queries)
if aws athena get-work-group --work-group "$WG_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "  delete workgroup $WG_NAME"
  aws athena delete-work-group --work-group "$WG_NAME" --region "$REGION" --recursive-delete-option || true
fi

# 4. Glue table + database
if aws glue get-table --database-name "$GLUE_DB" --name costs --region "$REGION" >/dev/null 2>&1; then
  echo "  delete glue table costs"
  aws glue delete-table --database-name "$GLUE_DB" --name costs --region "$REGION" || true
fi
if aws glue get-database --name "$GLUE_DB" --region "$REGION" >/dev/null 2>&1; then
  echo "  delete glue db $GLUE_DB"
  aws glue delete-database --name "$GLUE_DB" --region "$REGION" || true
fi

# 5. S3 bucket
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" 2>/dev/null; then
  echo "  empty + delete s3://$BUCKET"
  aws s3 rm "s3://$BUCKET" --recursive >/dev/null || true
  aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION" || true
fi

# 6. IAM role
if aws iam get-role --role-name "$LAMBDA_ROLE" >/dev/null 2>&1; then
  for p in $(aws iam list-role-policies --role-name "$LAMBDA_ROLE" --query 'PolicyNames' --output text); do
    aws iam delete-role-policy --role-name "$LAMBDA_ROLE" --policy-name "$p" || true
  done
  echo "  delete role $LAMBDA_ROLE"
  aws iam delete-role --role-name "$LAMBDA_ROLE" || true
fi

echo "[pipeline-cost-attribution] done"
