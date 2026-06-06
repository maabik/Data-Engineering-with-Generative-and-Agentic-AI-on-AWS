# data-quality-deequ-agent

Strands agent on **Bedrock AgentCore Runtime** that consumes the
[`data-architect-agent`](../data-architect-agent/) design JSON, runs
**Amazon Deequ** constraint suggestions + verification against the curated
table, registers the verification report as a Glue table queryable from
Athena, and emits a slim insights JSON describing what passed, what
failed, and whether the dataset is fit-for-use downstream.

The five MCP tools (`read_design`, `run_deequ_job`,
`register_report_table`, `query_report`, `derive_quality_insights`) live
behind one Lambda exposed via **AgentCore Gateway**.

```
   architect agent writes                          Cognito JWT
   s3://.../designs/<file>.json                          │
            │                                            ▼
            ▼                                     AgentCore Runtime  (data_quality_deequ_agent)
   EventBridge ──► data-quality-deequ-agent-bridge ────►     Strands agent container
                                                         │
                                                MCP-over-HTTP, JWT
                                                         ▼
                                          AgentCore Gateway (data-quality-deequ-agent-gw)
                                                         │
                                          Lambda invoke (GATEWAY_IAM_ROLE)
                                                         ▼
                                ┌────────────────────────────────────────┐
                                │  data-quality-deequ-agent-tools                    │
                                │  • read_design                         │
                                │  • run_deequ_job                       │
                                │  • register_report_table               │
                                │  • query_report                        │
                                │  • derive_quality_insights             │
                                └─────────────────┬──────────────────────┘
                                                  │  Glue StartJobRun
                                                  │  + S3 read/write
                                                  │  + Athena round-trip
                                                  ▼
                              Glue: data-quality-deequ-agent-deequ-job (PySpark + Deequ JAR)
                                  ConstraintSuggestionRunner (DEFAULT)
                                  → filter overly-aggressive checks
                                  → VerificationSuite
                                  → s3://.../dq-reports/<table>/{
                                        suggestions/<run>.json,
                                        results/<run>.json,
                                        parquet/run_id=<run>/}
                                                  │
                                                  ▼
                              Athena (workgroup data-quality-deequ-agent-wg) reads
                                  data_quality_deequ_agent_dq.dq_results
                                  (partition projection on run_id)
                                                  │
                                                  ▼
                              agent emits quality insights JSON
                              s3://${BUCKET}/dq-insights/<file>.json
```

## Why a chained agent

The data-architect-agent's job ends at *curation*: a typed Parquet table in
the Glue Catalog. Whether that table is *trustworthy* — whether the columns
the design promised actually look like the design said they would — is a
separate concern. Deequ is the right tool (column-level constraints,
suggestions from a profile, JVM-side execution at scale on Spark), but
wiring it up needs care:

1. **Suggestions, not just hand-written checks.** Deequ's
   `ConstraintSuggestionRunner` proposes the checks based on a profile
   pass; the agent + Glue script then *filter* aggressive ones (no
   `isComplete` on a column already 5% null, no allow-lists masquerading
   as enums) so the report is signal, not noise.
2. **Reports are written down.** The full per-constraint pass/fail rows
   are persisted both as JSON (for the agent's summary) and as Parquet
   under `dq-reports/<table>/parquet/run_id=…/`, registered in Glue with
   Athena partition projection so they're queryable instantly without
   `MSCK`.
3. **Insights, not just status codes.** The agent's final output is a
   single JSON object with the verdict, the failing constraints, and a
   one-paragraph summary aimed at the next agent (or human) — durable
   and reviewable in `s3://${BUCKET}/dq-insights/`.

## Tools

| Tool                       | Input                                                          | Output                                                                                          |
| -------------------------- | -------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `read_design`              | `design_uri`                                                   | `{ok, designUri, design:{...full design JSON...}}`                                              |
| `run_deequ_job`            | `database, table, run_id`                                      | `{ok, job, runId, state, suggestionsUri, resultsUri, parquetPrefix, error?}`                   |
| `register_report_table`    | `source_table, database?, table?`                              | `{ok, database, table, location, registered}`                                                   |
| `query_report`             | `query`                                                        | `{ok, queryExecutionId, columns[], rows[], rowCount}`                                           |
| `derive_quality_insights`  | `run_id, database, table`                                      | `{ok, runId, overallStatus, totalChecks, passedCount, failedCount, topFailing[], insightsUri}` |

`run_deequ_job` is the slow one: it starts the persistent `data-quality-deequ-agent-deequ-job`
PySpark Glue job, polls until terminal, and reports back the three S3
URIs (suggestions JSON, results JSON, Parquet prefix). The Glue script
is `glue/deequ_job.py`.

## Deequ JAR pinning

pydeequ 1.4.0 calls JVM-side methods that have shifted in newer Deequ
releases:

| Deequ JAR                | `Check.isComplete` 3-arg ctor | `RetainCompletenessRule()` no-arg ctor |
| ------------------------ | :---------------------------: | :------------------------------------: |
| `2.0.4-spark-3.3`        | ❌ (only 2-arg)                | ✅                                      |
| `2.0.7-spark-3.3` ✅      | ✅                             | ✅                                      |
| `2.0.10-spark-3.3`       | ✅                             | ❌ (promoted to private; `apply()` only) |

The deploy pins **`com.amazon.deequ:deequ:2.0.7-spark-3.3`**, downloads
the JAR locally, and uploads it to `s3://${BUCKET}/glue-scripts/jars/`,
then references it via the Glue job's `--extra-jars` argument. We do
*not* use `--conf spark.jars.packages` because Glue workers can't reach
Maven Central. Override with `DEEQU_JAR_COORD=…` if you need a different
ABI.

## Quality contract

The agent's final output (and the object written to
`s3://${BUCKET}/dq-insights/<file>.json`) is a single JSON object:

```json
{
  "database": "data_architect_agent_curated",
  "table": "violations_curated",
  "runId": "violations_curated_20250522",
  "suggestionsUri": "s3://.../dq-reports/violations_curated/suggestions/<run>.json",
  "resultsUri":     "s3://.../dq-reports/violations_curated/results/<run>.json",
  "parquetPrefix":  "s3://.../dq-reports/violations_curated/parquet/",
  "overallStatus": "Success",
  "totalChecks": 9, "passedCount": 9, "failedCount": 0,
  "topFailing": [],
  "athena": {
    "database": "data_quality_deequ_agent_dq",
    "table":    "dq_results",
    "sampleQuery": "SELECT \"constraint\", constraint_status, constraint_message FROM \"dqa_dq\".\"dq_results\" WHERE run_id = '...' ORDER BY constraint_status",
    "sampleRows": [ { "constraint": "...", "constraint_status": "Success", "constraint_message": "" } ]
  },
  "summary": "...",
  "dqInsightsUri": "s3://.../dq-insights/violations.json"
}
```

## Deploy

Prerequisites: AWS creds, `aws` v2, `jq`, and either `docker` (with buildx)
or `finch` for `linux/arm64` builds (AgentCore Runtime requires arm64).
**Deploy `data-profiling-agent` and `data-architect-agent` first** — this
project piggybacks on the shared S3 bucket (default
`data-profiling-agent-${ACCOUNT}-${REGION}`) and reads from the `data_architect_agent_curated` Glue
database.

```
./deploy.sh
```

Defaults (override via env): `AWS_REGION=us-east-1`, `PREFIX=data-quality-deequ-agent`,
`DATA_BUCKET=data-profiling-agent-${ACCOUNT}-${REGION}`,
`MODEL_ID=us.anthropic.claude-sonnet-4-6`, `DQ_DATABASE=data_quality_deequ_agent_dq`,
`DAA_DATABASE=data_architect_agent_curated`,
`DEEQU_JAR_COORD=com.amazon.deequ:deequ:2.0.7-spark-3.3`. Idempotent —
re-runs update everything in place.

The deploy creates: five IAM roles (lambda, bridge, glue, gateway,
runtime), a Cognito user pool / resource server / app client / test user,
a Secrets Manager secret, the `data_quality_deequ_agent_dq` Glue database, the `data-quality-deequ-agent-wg`
Athena workgroup, the persistent `data-quality-deequ-agent-deequ-job` Glue job (with the
Deequ JAR mounted via `--extra-jars`), the tools Lambda, an AgentCore
Gateway with a Lambda target and inline tool schema, an ECR repo + the
agent container image, the AgentCore Runtime, the bridge Lambda, an
EventBridge rule that fires on `s3:ObjectCreated` under `designs/`, and
Lake Formation grants on `default`, `data_architect_agent_curated`, and `data_quality_deequ_agent_dq` to the
Glue + Lambda roles.

## Live test

End-to-end chain: `landing/file.csv` → profiling-agent insights →
architect-agent design + curated Parquet → **data-quality-deequ-agent quality report**:

```
# (assumes data-profiling-agent and data-architect-agent already deployed)
aws s3 cp ../data-profiling-agent/sample/violations.csv \
    s3://data-profiling-agent-${ACCOUNT}-${REGION}/landing/violations.csv

# wait ~6-10 minutes for the chain (profile -> insights -> design -> curate
# -> deequ suggest+verify -> Athena round-trip), then:
aws s3 cp s3://data-profiling-agent-${ACCOUNT}-${REGION}/dq-insights/violations.json - | jq .

# inspect per-constraint pass/fail in Athena directly:
aws athena start-query-execution --work-group data-quality-deequ-agent-wg --region us-west-2 \
  --query-string 'SELECT "constraint", constraint_status FROM "data_quality_deequ_agent_dq"."dq_results" \
                  WHERE run_id LIKE ''violations_curated_%'' ORDER BY constraint_status'
```

You can also bypass EventBridge and invoke the runtime directly with a
known design URI:

```
RUNTIME_ARN=<from deploy output> SECRET_NAME=data-quality-deequ-agent/gateway-credentials \
AWS_REGION=us-east-1 \
  python scripts/invoke_agent.py s3://data-profiling-agent-${ACCOUNT}-${REGION}/designs/violations.json
```

## Layout

```
data-quality-deequ-agent/
├── deploy.sh                            # one-shot, idempotent
├── teardown.sh
├── tools/lambda_function.py             # MCP tools target
├── glue/deequ_job.py                    # PySpark + pydeequ suggest+verify
├── agent/
│   ├── data_quality_deequ_agent.py      # Strands @tool wrappers + entrypoint
│   ├── Dockerfile                       # python3.13 / arm64 / bedrock-agentcore
│   └── requirements.txt
├── bridge/lambda_function.py            # designs/ -> Cognito token -> Runtime
└── scripts/invoke_agent.py              # manual invocation helper
```

## Teardown

```
./teardown.sh
```

Deletes everything data-quality-deequ-agent created — EventBridge rule, both Lambdas, the
AgentCore Runtime, the Gateway and target, the ECR repo, the
`data-quality-deequ-agent-deequ-job` Glue job, the `data_quality_deequ_agent_dq` database and its tables, the
`data-quality-deequ-agent-wg` Athena workgroup, the Cognito pool, the Secrets Manager secret,
and all five IAM roles. The shared S3 bucket and the `data_architect_agent_curated`
database are left intact; set `DELETE_DQA_DATA=1` to also empty the
`dq-reports/`, `dq-insights/`, `athena-results/`, and the Deequ Glue
script under `glue-scripts/`.
