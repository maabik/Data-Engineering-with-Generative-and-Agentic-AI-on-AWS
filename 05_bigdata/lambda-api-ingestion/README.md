# Lambda API Ingestion (SQS → Lambda → S3)

A production-shaped batch-of-many ingest pattern: an SQS queue carries
ingest requests, a Lambda consumer fetches the API, and the response
lands in a partitioned S3 prefix with KMS encryption. Companion code for
the "ingestion at scale" section of chapter 05.

This is the more realistic counterpart to
`03_datalake/data-ingestion-to-s3/` — same idea (HTTP → S3) but adds:

- API key in **Secrets Manager** (not in env / source).
- Per-message destination — the queue body specifies which API,
  which secret, which bucket, which prefix.
- **Batch-item-failure** reporting (`batchItemFailures`) so SQS retries
  only the records that failed.
- Partitioned S3 keys — `dataset=<name>/ingest_date=<yyyy-mm-dd>/hour=<HH>/...`.
- Optional **SSE-KMS** server-side encryption.

## Files

| File | What it does |
|---|---|
| `lambda-api-ingestion.py` | Lambda handler — SQS event source, fetches one API per message, writes the response to S3 partitioned by `dataset/ingest_date/hour`. |
| `teardown.sh` | Deletes the SQS queue, Secrets Manager secret, Lambda + event-source mapping, and S3 objects under `raw/api/`. |

## SQS message body

Each SQS message body must be JSON with these fields:

```json
{
  "api_endpoint": "https://example.com/api/v1/",
  "api_query":   "endpoint?param=value",
  "secret_name": "dq-demo/api-key",
  "dataset":     "violations",
  "s3_bucket":   "dq-demo-123456789012-us-east-1",
  "s3_prefix":   "raw/api/"
}
```

The secret value must be JSON shaped like `{"api_key":"<value>"}`. The
Lambda sends `Authorization: Bearer <api_key>` on every call.

## Lambda environment variables

| Variable | Required | Purpose |
|---|---|---|
| `S3_KMS_KEY_ID` | no | If set, objects are written with `SSE-KMS` using this CMK. If unset, falls back to `AES256` (SSE-S3). |

## Prerequisites

- Shared `dq-demo` infrastructure from chapter 05's `_deploy/` (S3 bucket,
  Lambda execution role with `s3:PutObject`, `secretsmanager:GetSecretValue`,
  optional `kms:GenerateDataKey`, and `sqs:ReceiveMessage`/`DeleteMessage`).
- An SQS queue `dq-demo-api-ingest` wired as the Lambda event source.
- A Secrets Manager secret `dq-demo/api-key` with `{"api_key":"..."}`.

## Send a test message

```bash
QUEUE_URL=$(aws sqs get-queue-url --queue-name dq-demo-api-ingest \
              --query QueueUrl --output text)

aws sqs send-message --queue-url "$QUEUE_URL" --message-body "$(cat <<EOF
{
  "api_endpoint": "https://api.github.com",
  "api_query":    "/repos/aws/aws-cli",
  "secret_name":  "dq-demo/api-key",
  "dataset":      "github",
  "s3_bucket":    "dq-demo-${ACCOUNT}-us-east-1",
  "s3_prefix":    "raw/api/"
}
EOF
)"
```

Watch the object land:

```bash
aws s3 ls "s3://dq-demo-${ACCOUNT}-us-east-1/raw/api/dataset=github/" --recursive
```

## Teardown

```bash
bash teardown.sh
```

Deletes the queue, Lambda + event-source mapping, secret (force-deleted,
no recovery window), and the `raw/api/` S3 prefix.
**Does not** touch the shared bucket or IAM roles.

## Failure semantics

The handler returns `{"batchItemFailures":[{"itemIdentifier": "..."}]}`
for any message that raised an exception. Configure the event-source
mapping with `FunctionResponseTypes=["ReportBatchItemFailures"]` so SQS
retries only the failed items, not the whole batch.
