#!/usr/bin/env bash
# Teardown for glue-deequ-data-quality.
#
# Deletes:
#   - Glue ETL job              dq-demo-deequ-twitter
#   - S3 objects under          s3://${BUCKET}/raw/twitter/
#                               s3://${BUCKET}/scripts/glue-deequ-job.py
#                               s3://${BUCKET}/jars/deequ-*.jar
#                               s3://${BUCKET}/temp/   (Glue scratch)
#   - Glue CloudWatch log streams for this job's runs (best-effort)
#
# Does NOT delete the shared S3 bucket or IAM roles.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-dq-demo}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"

JOB_NAME="${PREFIX}-deequ-twitter"

echo "[glue-deequ-data-quality] tearing down (region=$REGION account=$ACCOUNT)"

# 1. Stop in-flight runs
for rid in $(aws glue get-job-runs --job-name "$JOB_NAME" --region "$REGION" \
              --query 'JobRuns[?JobRunState==`RUNNING` || JobRunState==`STARTING` || JobRunState==`WAITING`].Id' \
              --output text 2>/dev/null); do
    [ -n "$rid" ] && [ "$rid" != "None" ] || continue
    echo "  stop run $rid"
    aws glue batch-stop-job-run --job-name "$JOB_NAME" --job-run-ids "$rid" --region "$REGION" >/dev/null || true
done

# 2. Glue job
if aws glue get-job --job-name "$JOB_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "  delete glue job $JOB_NAME"
    aws glue delete-job --job-name "$JOB_NAME" --region "$REGION" >/dev/null
fi

# 3. S3 artefacts
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null 2>&1; then
    for p in "raw/twitter/" "temp/"; do
        echo "  rm s3://$BUCKET/$p"
        aws s3 rm "s3://$BUCKET/$p" --recursive >/dev/null || true
    done
    aws s3 rm "s3://$BUCKET/scripts/glue-deequ-job.py" >/dev/null 2>&1 || true
    aws s3 rm "s3://$BUCKET/jars/" --recursive --exclude "*" --include "deequ-*.jar" >/dev/null 2>&1 || true
fi

echo "[glue-deequ-data-quality] done"
