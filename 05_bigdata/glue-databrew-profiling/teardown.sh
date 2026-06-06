#!/usr/bin/env bash
# Teardown for glue-databrew-profiling.
#
# Deletes:
#   - DataBrew profile job      dq-demo-violations-profile
#   - DataBrew dataset          dq-demo-violations-dataset
#   - Lambda function           dq-demo-databrew-profile
#   - S3 objects under          s3://${BUCKET}/profile/violations/
#                               s3://${BUCKET}/raw/violations/
#
# Does NOT delete the shared S3 bucket or IAM roles.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-dq-demo}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"

JOB_NAME="${PREFIX}-violations-profile"
DATASET_NAME="${PREFIX}-violations-dataset"
LAMBDA_NAME="${PREFIX}-databrew-profile"

echo "[glue-databrew-profiling] tearing down (region=$REGION account=$ACCOUNT)"

# 1. Stop in-flight DataBrew runs (best-effort)
for run in $(aws databrew list-job-runs --name "$JOB_NAME" --region "$REGION" \
              --query 'JobRuns[?State==`RUNNING` || State==`STARTING`].RunId' --output text 2>/dev/null); do
    [ -n "$run" ] && [ "$run" != "None" ] || continue
    echo "  stop in-flight run $run"
    aws databrew stop-job-run --name "$JOB_NAME" --run-id "$run" --region "$REGION" >/dev/null || true
done

# 2. DataBrew profile job
if aws databrew describe-job --name "$JOB_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "  delete databrew job $JOB_NAME"
    aws databrew delete-job --name "$JOB_NAME" --region "$REGION" >/dev/null || true
fi

# 3. DataBrew dataset
if aws databrew describe-dataset --name "$DATASET_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "  delete databrew dataset $DATASET_NAME"
    aws databrew delete-dataset --name "$DATASET_NAME" --region "$REGION" >/dev/null || true
fi

# 4. Lambda
if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "  delete lambda $LAMBDA_NAME"
    aws lambda delete-function --function-name "$LAMBDA_NAME" --region "$REGION"
fi

# 5. S3 prefixes
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null 2>&1; then
    for p in "profile/violations/" "raw/violations/"; do
        echo "  rm s3://$BUCKET/$p"
        aws s3 rm "s3://$BUCKET/$p" --recursive >/dev/null || true
    done
fi

echo "[glue-databrew-profiling] done"
