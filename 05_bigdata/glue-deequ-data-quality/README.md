# Glue + PyDeequ Data Quality

A Glue PySpark job that runs a [PyDeequ](https://github.com/awslabs/python-deequ)
analysis suite and verification suite over a CSV dataset, then optionally
appends both result sets to Iceberg tables for trending. Companion code
for the Deequ-based DQ section of chapter 05.

The same dataset is used by `data-quality-deequ-agent/` in the same
chapter — the agent generates Deequ checks; this job runs them as part
of a real Glue pipeline.

## Files

| File | What it does |
|---|---|
| `glue-deequ-job.py` | Glue PySpark script — column normalization, `AnalysisRunner` (size, completeness, distinctness), `VerificationSuite` (size ≥ 3, completeness, uniqueness), optional Iceberg persistence. |
| `teardown.sh` | Deletes the Glue job and the S3 artefacts under `raw/twitter/`, `scripts/`, `jars/`, and `temp/`. |

## Job arguments

| Arg | Required | Purpose |
|---|---|---|
| `--JOB_NAME` | yes | Glue job name (set automatically by Glue). |
| `--S3_INPUT` | yes | Input CSV — `s3://bucket/path/to/file.csv`. |
| `--RESULTS_TABLE` | no | Fully qualified Iceberg/Glue table for results (`catalog.namespace.table`). If set, the job appends to `<table>_metrics` and `<table>_checks`. If omitted, results are only logged. |
| `--SPARK_VERSION` | no | Spark major.minor matching the Glue version. Default `3.3` (Glue 4.0). |

The job exits non-zero if the verification suite fails — wire that to your
orchestrator (Step Functions, Airflow) to halt downstream pipelines on a
DQ failure.

## Prerequisites

- Shared `dq-demo` infrastructure (S3 bucket, Glue role) from chapter 05's
  `_deploy/`.
- The PyDeequ jar uploaded to `s3://${BUCKET}/jars/deequ-*.jar` so it can
  be referenced via `--extra-jars`. The version must match the Glue Spark
  runtime — Glue 4.0 (Spark 3.3) needs `deequ-2.0.x-spark-3.3.jar`.
- The script uploaded to `s3://${BUCKET}/scripts/glue-deequ-job.py`.

## Run

```bash
aws glue create-job \
    --name dq-demo-deequ-twitter \
    --role arn:aws:iam::<account>:role/dq-demo-glue-role \
    --command "Name=glueetl,ScriptLocation=s3://${BUCKET}/scripts/glue-deequ-job.py,PythonVersion=3" \
    --glue-version 4.0 \
    --default-arguments "{
        \"--extra-jars\":\"s3://${BUCKET}/jars/deequ-2.0.7-spark-3.3.jar\",
        \"--additional-python-modules\":\"pydeequ==1.4.0\",
        \"--TempDir\":\"s3://${BUCKET}/temp/\"
    }"

aws glue start-job-run \
    --job-name dq-demo-deequ-twitter \
    --arguments "--S3_INPUT=s3://${BUCKET}/raw/twitter/tweets.csv"
```

## Teardown

```bash
bash teardown.sh
```

Deletes the Glue job and the S3 artefacts (`raw/twitter/`, `temp/`,
`scripts/glue-deequ-job.py`, `jars/deequ-*.jar`). **Does not** touch the
shared bucket, IAM roles, or the Iceberg results database — they are
owned by the chapter-level `_deploy/`.

If you used `--RESULTS_TABLE`, drop the metric and check tables manually:

```sql
DROP TABLE catalog.namespace.results_metrics PURGE;
DROP TABLE catalog.namespace.results_checks PURGE;
```
