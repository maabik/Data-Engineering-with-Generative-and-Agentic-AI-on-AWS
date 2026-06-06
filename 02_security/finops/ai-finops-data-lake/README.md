# ai-finops-data-lake

## 1 — Overview

The FinOps data lake substrate that every other project queries. Joins five cost and usage signals (Bedrock audit trail, CUR 2.0, inference profiles, AgentCore Runtime, adjacent services) into a single Iceberg dataset partitioned by date, model family, and tenant.

---

## 2 — What it ingests

| Source | Signal | How |
|---|---|---|
| (a) Bedrock audit trail | Invocation logs with token counts, latency, guardrail events, requestMetadata | S3 NDJSON (bedrock-audit-trail output) |
| (b) CUR 2.0 | Actual billed costs with `line_item_iam_principal` and cost-allocation tags | S3 Parquet (CUR 2.0 delivery) |
| (c) Inference profiles | Application profile → model mapping and tags | Bedrock `ListInferenceProfiles` API |
| (d) AgentCore Runtime | Per-run token counts, tool call counts, agent metadata | CloudWatch Logs (`/aws/bedrock/agentcore/runtime`) |
| (e) Adjacent services | Knowledge Base, OpenSearch Serverless, S3 Vectors, Guardrail charges | CUR 2.0 (re-labeled by service_category) or Cost Explorer fallback |

---

## 3 — Architecture (ASCII)

```
(a) S3: audit-trail NDJSON ──┐
(b) S3: CUR 2.0 Parquet ─────┤
(c) Bedrock API profiles ─────┼── pipeline/runner.py ── join.py ── writer.py
(d) CloudWatch AgentCore ────┤                                        │
(e) CUR adjacent services ───┘                                        ▼
                                                          S3 Iceberg bucket
                                                          ai_finops.invocations
                                                          partitioned by:
                                                            date / model_family / tenant_id
                                                                      │
                                                          Glue Catalog + Athena views
                                                                      │
                                                          QuickSight Cost Cockpit
```

---

## 4 — Prerequisites

- Python >= 3.11
- AWS account, region `us-west-2`
- CDK CLI + bootstrap: `npm install -g aws-cdk` then `cdk bootstrap aws://ACCOUNT_ID/us-west-2`
- `pip install aws-cdk-lib constructs`
- A Bedrock model invocation log S3 bucket (the `BedrockLogBucketName` CDK parameter)

---

## 5 — Installation

```bash
pip install -e .
pip install -e ".[dev]"
```

---

## 6 — Quick start

```bash
cd 02_security/finops/ai-finops-data-lake

# Run all tests (no AWS creds needed)
pip install -e ".[dev]"
pytest

# Deploy CDK stack
cd cdk
cdk deploy AIFinOpsLake \
  --parameters BedrockLogBucketName=my-bedrock-audit-bucket \
  --parameters CurBucketName=my-cur-bucket \
  --parameters CurPrefix=cur/bedrock/ \
  --parameters AlarmEmail=finops@example.com

# Run the pipeline manually (or trigger the daily Lambda)
python -m pipeline.runner \
  --start-date 2026-01-01 \
  --end-date   2026-01-31 \
  --bedrock-log-bucket my-bedrock-audit-bucket \
  --cur-bucket         my-cur-bucket \
  --cur-prefix         cur/bedrock/ \
  --athena-output      s3://my-athena-results/finops/ \
  --staging-bucket     my-staging-bucket \
  --dry-run
```

---

## 7 — Components

### `pipeline/sources/`

| Module | Source |
|---|---|
| `bedrock_logs.py` | Reads NDJSON/.gz from S3; applies per-model pricing table; yields `bedrock_invocation` records |
| `cur.py` | Reads CUR 2.0 Parquet with PyArrow; maps cost-allocation tags to tenant/workload |
| `inference_profiles.py` | Fetches all Bedrock inference profiles; `enrich_record()` back-fills profile name/tenant |
| `agentcore.py` | Reads AgentCore Runtime execution summaries from CloudWatch Logs |
| `adjacent_services.py` | Labels CUR records by `service_category` (bedrock_kb, opensearch, s3_vectors, bedrock_guardrail); Cost Explorer fallback |

### `pipeline/join.py`

Correlates invocation records with CUR line items using three strategies:
1. **Exact** — `request_id` = `line_item_resource_id`
2. **Fuzzy token** — same date/account/model bucket + token counts within ±10%
3. **No match** — `actual_cost_usd = null`, `joined = false`

### `pipeline/writer.py`

Issues `INSERT INTO` via Athena Iceberg DML. Batches at 5000 records per
query to stay under Athena limits. For large backfills use the Glue job.

### `pipeline/runner.py`

Orchestrates all sources → join → enrich → write. Usable as a CLI,
a Lambda handler (`pipeline/daily_runner.py`), or a Glue Python Shell job
(`glue/jobs/backfill_job.py`).

---

## 8 — Iceberg schema (key fields)

| Column | Type | Notes |
|---|---|---|
| `date` | date | Partition key |
| `model_family` | string | Partition key |
| `tenant_id` | string | Partition key |
| `source` | string | `bedrock_invocation \| cur \| agentcore_runtime \| cur_bedrock_kb \| ...` |
| `service_category` | string | `bedrock \| bedrock_kb \| opensearch \| s3_vectors \| agentcore \| ...` |
| `estimated_cost_usd` | double | Token counts × pricing table |
| `actual_cost_usd` | double | From CUR (null when not joined) |
| `iam_principal` | string | From CUR `line_item_iam_principal` |
| `inference_profile_id/name` | string | Enriched from Bedrock API |
| `joined` | boolean | Whether a CUR line item was matched |
| `join_method` | string | `exact_request_id \| fuzzy_token \| none` |

Full schema: [`athena/ddl/create_invocations_table.sql`](athena/ddl/create_invocations_table.sql)

---

## 9 — Athena views

| View | Purpose |
|---|---|
| `v_daily_cost_by_tenant` | Top-line cost rollup by tenant/service/day — primary QuickSight source |
| `v_model_spend_share` | Each model's spend share % within a tenant/day |
| `v_adjacent_services` | KB, OpenSearch, S3 Vectors, Guardrail cost breakdown |
| `v_cost_vs_estimated` | Reconciliation: estimate vs. actual CUR cost, variance %, join rate |

---

## 10 — CDK deployment

Stack name: `AIFinOpsLake`, region: `us-west-2`.

```bash
cd cdk
pip install aws-cdk-lib constructs
cdk bootstrap aws://ACCOUNT_ID/us-west-2   # first time only
cdk deploy AIFinOpsLake \
  --parameters BedrockLogBucketName=MY-BEDROCK-LOG-BUCKET \
  --parameters CurBucketName=MY-CUR-BUCKET \
  --parameters CurPrefix=cur/bedrock/ \
  --parameters AlarmEmail=finops@example.com
```

Parameters:

| Parameter | Required | Description |
|---|---|---|
| `BedrockLogBucketName` | yes | S3 bucket containing Bedrock audit trail NDJSON logs |
| `CurBucketName` | no | S3 bucket for CUR 2.0 Parquet exports; leave blank to use Cost Explorer |
| `CurPrefix` | no | S3 prefix within `CurBucketName` |
| `AlarmEmail` | no | Email for cost anomaly CloudWatch alarms |

Resources provisioned:

| Resource | Details |
|---|---|
| KMS CMK | `alias/ai-finops-lake-key`, rotation enabled, RETAIN on delete |
| S3 Iceberg bucket | KMS-encrypted, versioned, 90-day IA transition, RETAIN |
| S3 staging bucket | KMS-encrypted, 3-day expiry, auto-delete |
| S3 Athena results bucket | KMS-encrypted, 7-day expiry, auto-delete |
| Athena workgroup | `ai-finops`, 10 GB per-query scan limit, results in Athena bucket |
| Glue database | `ai_finops` |
| Glue table | `invocations` (Iceberg type) |
| Lambda daily runner | Python 3.12, 15-min timeout, triggered at 02:00 UTC |
| Glue backfill job | Python Shell, 1 DPU, for historical backfills |
| IAM pipeline role | Least-privilege for Lambda + Glue (S3, Athena, Glue, Bedrock, CE) |

CloudFormation outputs:

| Output | Description |
|---|---|
| `IcebergBucketName` | Main data lake bucket |
| `StagingBucketName` | Athena INSERT staging |
| `AthenaResultsBucketName` | Athena query results |
| `AthenaWorkgroupName` | `ai-finops` |
| `GlueDatabaseName` | `ai_finops` |
| `LakeCmkArn` | KMS key ARN |
| `PipelineRoleArn` | IAM role for Lambda/Glue |
| `DailyRunnerFunctionName` | Lambda function name |

---

## 11 — Unit tests

```bash
pytest tests/ -v    # 53 tests, no AWS credentials needed
```

---

## 12 — Live integration tests

Requires deployed `AIFinOpsLake` stack and AWS credentials in `us-west-2`:

```bash
cd ai-finops-data-lake
pytest tests/test_live.py -v
```

13 tests:
- `test_stack_is_complete` — CloudFormation stack status is `CREATE_COMPLETE` or `UPDATE_COMPLETE`
- `test_iceberg_bucket_exists_and_encrypted` — S3 bucket present, SSE-KMS rule set
- `test_staging_bucket_exists` — head_bucket returns 200
- `test_athena_results_bucket_exists` — head_bucket returns 200
- `test_lake_cmk_enabled_with_rotation` — KMS key `Enabled`, rotation on
- `test_glue_database_exists` — `ai_finops` database in Glue catalog
- `test_glue_invocations_table_is_iceberg` — `table_type == ICEBERG` in Glue parameters
- `test_glue_invocations_table_has_expected_columns` — required columns present in StorageDescriptor
- `test_athena_workgroup_exists_and_configured` — workgroup `ENABLED`, `EnforceWorkGroupConfiguration=True`, 10 GB scan limit
- `test_daily_runner_lambda_exists` — function `Active`, runtime Python 3.12, timeout 900s
- `test_daily_runner_lambda_has_required_env_vars` — all five required env vars present
- `test_pipeline_role_exists` — IAM role exists
- `test_glue_backfill_job_exists` — job command is `pythonshell`, capacity 1.0 DPU

---

## 13 — Pricing table

The `DEFAULT_PRICING` dict in `pipeline/sources/bedrock_logs.py` covers 15
model families. Override per-model rates at deploy time by passing
`pricing_overrides` to `bedrock_logs.extract()` or setting them as environment
variables on the Lambda.

---

## 14 — Project structure

```
ai-finops-data-lake/
├── pipeline/
│   ├── sources/
│   │   ├── bedrock_logs.py
│   │   ├── cur.py
│   │   ├── inference_profiles.py
│   │   ├── agentcore.py
│   │   └── adjacent_services.py
│   ├── join.py
│   ├── writer.py
│   ├── runner.py
│   └── daily_runner.py          # Lambda handler
├── glue/
│   └── jobs/
│       └── backfill_job.py
├── athena/
│   ├── ddl/
│   │   └── create_invocations_table.sql
│   └── views/
│       ├── v_daily_cost_by_tenant.sql
│       ├── v_model_spend_share.sql
│       ├── v_adjacent_services.sql
│       └── v_cost_vs_estimated.sql
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   └── stacks/
│       └── finops_lake_stack.py    # AIFinOpsLakeStack
├── tests/
│   ├── [unit test files]           # 53 unit tests
│   └── test_live.py                # 13 live tests
└── pyproject.toml
```
