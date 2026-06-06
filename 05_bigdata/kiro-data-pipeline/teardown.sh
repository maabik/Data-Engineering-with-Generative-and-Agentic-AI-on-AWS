#!/usr/bin/env bash
# Teardown for kiro-data-pipeline.
#
# Deletes:
#   - Glue ETL job              dq-demo-violations-pipeline
#   - Glue catalog database     dq_demo_violations (with its Iceberg tables)
#   - S3 objects under          s3://${BUCKET}/lakehouse/        (Iceberg data + metadata)
#                               s3://${BUCKET}/scripts/violations_pipeline.py
#                               s3://${BUCKET}/jars/iceberg-*.jar
#
# Does NOT delete the shared S3 bucket or IAM roles.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-dq-demo}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"

JOB_NAME="${PREFIX}-violations-pipeline"
DB_NAME="dq_demo_violations"

echo "[kiro-data-pipeline] tearing down (region=$REGION account=$ACCOUNT)"

# 1. Stop in-flight runs
for rid in $(aws glue get-job-runs --job-name "$JOB_NAME" --region "$REGION" \
              --query 'JobRuns[?JobRunState==`RUNNING` || JobRunState==`STARTING` || JobRunState==`WAITING`].Id' \
              --output text 2>/dev/null); do
    [ -n "$rid" ] && [ "$rid" != "None" ] || continue
    echo "  stop run $rid"
    aws glue batch-stop-job-run --job-name "$JOB_NAME" --job-run-ids "$rid" --region "$REGION" >/dev/null || true
done

# 2. Glue ETL job
if aws glue get-job --job-name "$JOB_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "  delete glue job $JOB_NAME"
    aws glue delete-job --job-name "$JOB_NAME" --region "$REGION" >/dev/null
fi

# 3. Glue catalog database (delete-database cascades to its tables).
#    On accounts where Lake Formation is enforcing permissions, the caller
#    needs DROP on the database. Try once; if AccessDenied, self-grant DROP
#    via Lake Formation (works if caller is an LF admin or account admin)
#    and retry.
if aws glue get-database --name "$DB_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "  delete glue database $DB_NAME (cascades to tables)"
    if ! aws glue delete-database --name "$DB_NAME" --region "$REGION" 2>/tmp/dq-demo-lf-err; then
        if grep -q "Lake Formation" /tmp/dq-demo-lf-err 2>/dev/null; then
            CALLER_ARN="$(aws sts get-caller-identity --query Arn --output text)"
            ROLE_ARN="$(printf '%s' "$CALLER_ARN" | sed 's|sts::|iam::|; s|:assumed-role/|:role/|; s|/[^/]*$||')"
            echo "  Lake Formation blocking delete; granting DROP/DESCRIBE to $ROLE_ARN"
            aws lakeformation grant-permissions --region "$REGION" \
                --principal "DataLakePrincipalIdentifier=$ROLE_ARN" \
                --resource "{\"Database\":{\"Name\":\"$DB_NAME\"}}" \
                --permissions DROP DESCRIBE >/dev/null 2>&1 || true
            for tbl in $(aws glue get-tables --database-name "$DB_NAME" --region "$REGION" \
                          --query 'TableList[].Name' --output text 2>/dev/null); do
                [ -n "$tbl" ] && [ "$tbl" != "None" ] || continue
                aws lakeformation grant-permissions --region "$REGION" \
                    --principal "DataLakePrincipalIdentifier=$ROLE_ARN" \
                    --resource "{\"Table\":{\"DatabaseName\":\"$DB_NAME\",\"Name\":\"$tbl\"}}" \
                    --permissions DROP DESCRIBE >/dev/null 2>&1 || true
            done
            aws glue delete-database --name "$DB_NAME" --region "$REGION"
        else
            cat /tmp/dq-demo-lf-err >&2
            exit 1
        fi
    fi
fi

# 4. S3 artefacts (Iceberg warehouse + scripts + jars)
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null 2>&1; then
    echo "  rm s3://$BUCKET/lakehouse/"
    aws s3 rm "s3://$BUCKET/lakehouse/" --recursive >/dev/null || true
    aws s3 rm "s3://$BUCKET/scripts/violations_pipeline.py" >/dev/null 2>&1 || true
    aws s3 rm "s3://$BUCKET/jars/" --recursive --exclude "*" --include "iceberg-*.jar" >/dev/null 2>&1 || true
fi

echo "[kiro-data-pipeline] done"
