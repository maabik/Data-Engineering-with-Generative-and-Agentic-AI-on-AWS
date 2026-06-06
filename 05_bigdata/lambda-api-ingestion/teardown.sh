#!/usr/bin/env bash
# Teardown for lambda-api-ingestion.
#
# Deletes:
#   - SQS queue                 dq-demo-api-ingest
#   - Secrets Manager secret    dq-demo/api-key
#   - Lambda function           dq-demo-api-ingest
#   - Lambda event source mapping for the SQS queue
#   - S3 objects under          s3://${BUCKET}/raw/api/
#
# Does NOT delete the shared S3 bucket or IAM roles — those belong to
# _deploy/teardown-shared.sh.
#
# Required env (or defaults):
#   AWS_REGION   (default us-east-1)
#   BUCKET       (default dq-demo-${ACCOUNT_ID}-${AWS_REGION})
#   PREFIX       (default dq-demo)
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-dq-demo}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"

QUEUE_NAME="${PREFIX}-api-ingest"
LAMBDA_NAME="${PREFIX}-api-ingest"
SECRET_NAME="${PREFIX}/api-key"

echo "[lambda-api-ingestion] tearing down (region=$REGION account=$ACCOUNT)"

# 1. Event source mappings -> Lambda
for uuid in $(aws lambda list-event-source-mappings --function-name "$LAMBDA_NAME" --region "$REGION" \
                --query 'EventSourceMappings[].UUID' --output text 2>/dev/null); do
    [ -n "$uuid" ] && [ "$uuid" != "None" ] || continue
    echo "  delete event-source-mapping $uuid"
    aws lambda delete-event-source-mapping --uuid "$uuid" --region "$REGION" >/dev/null || true
done

# 2. Lambda
if aws lambda get-function --function-name "$LAMBDA_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "  delete lambda $LAMBDA_NAME"
    aws lambda delete-function --function-name "$LAMBDA_NAME" --region "$REGION"
fi

# 3. SQS queue
QUEUE_URL="$(aws sqs get-queue-url --queue-name "$QUEUE_NAME" --region "$REGION" \
              --query QueueUrl --output text 2>/dev/null || true)"
if [ -n "$QUEUE_URL" ] && [ "$QUEUE_URL" != "None" ]; then
    echo "  delete queue $QUEUE_URL"
    aws sqs delete-queue --queue-url "$QUEUE_URL" --region "$REGION"
fi

# 4. Secret (force, no recovery window)
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
    echo "  delete secret $SECRET_NAME"
    aws secretsmanager delete-secret --secret-id "$SECRET_NAME" --region "$REGION" \
        --force-delete-without-recovery >/dev/null
fi

# 5. S3 data prefix
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null 2>&1; then
    echo "  rm s3://$BUCKET/raw/api/"
    aws s3 rm "s3://$BUCKET/raw/api/" --recursive >/dev/null || true
fi

echo "[lambda-api-ingestion] done"
