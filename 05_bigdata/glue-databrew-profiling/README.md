# Glue DataBrew Profiling

A Lambda + DataBrew pattern for repeatable, encrypted column-level profiling.
Companion code for the data-quality / profiling section of chapter 05.

The Lambda **idempotently** creates (or updates) a DataBrew profile job,
configures column-level statistics, and optionally encrypts the profile
output with a Customer Managed KMS key.

## Files

| File | What it does |
|---|---|
| `lambda-create-profile-job.py` | Lambda handler — `databrew:CreateProfileJob` with full column-statistics set; falls back to `UpdateProfileJob` on `ConflictException` for idempotency. |
| `teardown.sh` | Deletes the profile job, dataset, Lambda, and S3 outputs under `raw/` and `profile/`. |

## Profile statistics collected

`DISTINCT_VALUES_COUNT`, `UNIQUE_VALUES_COUNT`, `VALUE_DISTRIBUTION`,
`PERCENTILES`, `RANGE`, `SKEWNESS`, `STANDARD_DEVIATION`, `VARIANCE`,
`MEAN`, `MEDIAN`, `MODE`, `MIN`, `MAX`, `MISSING_VALUES_COUNT`.

## Prerequisites

- Shared `dq-demo` infrastructure from chapter 05's `_deploy/` (S3 bucket,
  IAM role for DataBrew, optional KMS CMK).
- A registered DataBrew dataset (the deploy step in the chapter creates
  `dq-demo-violations-dataset` over `s3://${BUCKET}/raw/violations/`).

## Configure (Lambda environment variables)

| Variable | Required | Purpose |
|---|---|---|
| `JOB_NAME` | yes | DataBrew profile job name (e.g. `dq-demo-violations-profile`). |
| `DATASET_NAME` | yes | DataBrew dataset to profile. |
| `ROLE_ARN` | yes | IAM role the profile job assumes. |
| `OUTPUT_BUCKET` | yes | S3 bucket for the profile output. |
| `OUTPUT_KEY_PREFIX` | yes | Key prefix inside the bucket (e.g. `profile/violations/`). |
| `KMS_KEY_ARN` | no | If set, output is encrypted with `SSE-KMS` using this CMK. |
| `PROFILE_FULL_DATASET` | no | `"true"` to profile the full dataset; default samples 20 000 rows. |

## Invoke

```bash
aws lambda invoke \
  --function-name dq-demo-databrew-profile \
  --payload '{}' /tmp/out.json
cat /tmp/out.json   # -> {"job":"dq-demo-violations-profile","action":"created"}
```

Then run the profile job:

```bash
aws databrew start-job-run --name dq-demo-violations-profile
aws databrew list-job-runs --name dq-demo-violations-profile
```

The profile JSON lands at
`s3://${OUTPUT_BUCKET}/${OUTPUT_KEY_PREFIX}<job-run-id>.json`.

## Teardown

```bash
bash teardown.sh
```

Deletes the profile job, the DataBrew dataset, the Lambda function, and
the S3 prefixes `raw/violations/` and `profile/violations/`.
**Does not** touch the shared bucket, KMS key, or IAM roles — those are
owned by `_deploy/teardown-shared.sh` at the chapter level.

## Cost notes

- DataBrew profile jobs bill per node-hour (default 5 nodes). A 20K-row
  sample finishes in 1-2 minutes on the default node count; full-dataset
  profiling scales linearly with input size.
- Profile output is small (single JSON document per run) — the SSE-KMS
  charge is one `kms:GenerateDataKey` call per object.
