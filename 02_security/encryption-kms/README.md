# S3 + KMS Customer Managed Key (CMK)

A short notebook that creates a KMS Customer Managed Key and an S3 bucket
configured to use it for default server-side encryption (SSE-KMS).

This is the worked example for the encryption-at-rest section of chapter 02.
It is intentionally minimal — boto3 calls only, no infrastructure-as-code —
so the API surface for `kms:CreateKey` and `s3:PutBucketEncryption` is easy
to read.

## Files

| File | What it does |
|---|---|
| `s3-kms-cmk.ipynb` | Creates a CMK (`kms:CreateKey`) and an S3 bucket whose default encryption uses that CMK (`s3:PutBucketEncryption` with `SSEAlgorithm=aws:kms`). |

## Prerequisites

- AWS credentials with permission to create KMS keys and S3 buckets.
- `boto3` (`pip install boto3` or run inside a SageMaker / Glue notebook).
- A globally unique S3 bucket name — the default in the notebook
  (`cmk-encrypted-bucket-using-sdk`) will collide; change it before running.

## Run

Open the notebook and run the cells top-to-bottom. The final cell prints the
created CMK ARN and the bucket name.

## Teardown

Clean up after the demo to avoid the standing $1/month KMS key charge:

```bash
# Replace with the values printed by the notebook
BUCKET=cmk-encrypted-bucket-using-sdk
KEY_ID=<key-id from the printed CMK ARN>

aws s3 rb "s3://$BUCKET" --force
# Schedule key deletion (7-30 day waiting period; 7 is the minimum)
aws kms schedule-key-deletion --key-id "$KEY_ID" --pending-window-in-days 7
```

KMS keys cannot be deleted immediately — they enter a pending-deletion
window so any data still encrypted with the key can be recovered. During
that window the key continues to incur its monthly charge.
