# data-architect-agent

Strands agent on **Bedrock AgentCore Runtime** that consumes the
[`data-profiling-agent`](../data-profiling-agent/) insights JSON, designs
the schema for a new dataset, registers raw + curated tables in the
Glue Data Catalog, and runs the first ETL job to materialise curated
Parquet — all in a single agent invocation.

The four MCP tools (`read_insights`, `create_glue_database`,
`create_glue_table`, `run_glue_job`) live behind one Lambda exposed via
**AgentCore Gateway**.

```
   profiling agent writes                         Cognito JWT
   s3://.../insights/<file>.json                       │
            │                                          ▼
            ▼                                   AgentCore Runtime  (data_architect_agent)
   EventBridge ──► data-architect-agent-bridge ─►  Strands agent container
                                                       │
                                              MCP-over-HTTP, JWT
                                                       ▼
                                          AgentCore Gateway (data-architect-agent-gw)
                                                       │
                                          Lambda invoke (GATEWAY_IAM_ROLE)
                                                       ▼
                                  ┌────────────────────────────────────┐
                                  │  data-architect-agent-tools        │
                                  │  • read_insights                   │
                                  │  • create_glue_database            │
                                  │  • create_glue_table               │
                                  │  • run_glue_job                    │
                                  └─────────────────┬──────────────────┘
                                                    │ Glue create/update
                                                    │ + Glue job start/poll
                                                    ▼
                                  Glue Catalog: <db>.<table>_raw (CSV)
                                                <db>.<table>_curated (Parquet)
                                                    │
                                                    ▼
                                  s3://${BUCKET}/curated/<table>/<partitions>/
                                                    │
                                                    ▼
                                  agent emits design JSON
                                  s3://${BUCKET}/designs/<file>.json
```

## Why a chained agent

The data-profiling-agent's job ends at *insights*: a typed JSON describing
what the dataset looks like. Turning insights into a *cataloged + curated*
dataset is a separate concern — schema choices, partition strategy, ETL
script wiring, run-and-poll bookkeeping. Letting an agent do this means:

1. **Decisions are written down.** The agent's design JSON (under
   `s3://${BUCKET}/designs/`) records which columns became partitions and
   why, the chosen Glue types, and the ETL job ID — a durable artifact for
   the next agent in a longer chain (e.g. a Data Quality or BI Agent).
2. **Tools, not prompts.** Glue catalog updates and job execution are
   side-effecting and finicky; the agent reasons in JSON and the Lambda
   handles all the AWS API quirks (idempotent create-or-update, polling,
   etc).
3. **Reuse.** The same Gateway tool surface can be called by any other
   Strands or Bedrock agent in the account using the same auth model.

## Tools

| Tool                    | Input                                                                                  | Output                                                                       |
| ----------------------- | -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `read_insights`         | `insights_uri`                                                                         | `{ok, insightsUri, insights:{...full JSON...}}`                              |
| `create_glue_database`  | `database`                                                                             | `{ok, database, created}`                                                    |
| `create_glue_table`     | `database, table, columns[], location, partitions[], format`                           | `{ok, database, table, created, location, format, columns, partitionKeys}`   |
| `run_glue_job`          | `job_name, role_arn, script_uri, args, timeout_minutes?`                               | `{ok, job, runId, state, error?, logGroupName, args}`                        |

`run_glue_job` is the slow one: it create-or-updates a `glueetl` PySpark
job (`glue/curate_job.py`), starts a run, and polls until terminal. The
script reads CSV/JSON/Parquet at `--SOURCE_URI`, writes Parquet to
`--TARGET_URI` partitioned by `--PARTITION_COLS`, and runs
`MSCK REPAIR TABLE` against `--DATABASE.--TABLE` so curated partitions show up.

## Design contract

The agent's final output (and the object written to
`s3://${BUCKET}/designs/<file>.json`) is a single JSON object with these
keys:

```json
{
  "database": "data_architect_agent_curated",
  "rawTable": "violations_raw",
  "curatedTable": "violations_curated",
  "location": "s3://.../curated/violations/",
  "partitionKeys": [{ "name": "violation_date", "type": "date" }],
  "columns": [
    { "name": "id", "type": "bigint" },
    { "name": "fine", "type": "double" },
    { "name": "respondents", "type": "string" }
  ],
  "glueJob": { "ok": true, "job": "data-architect-agent-curate-violations", "runId": "...", "state": "SUCCEEDED" },
  "insightsUri": "s3://.../insights/violations.json",
  "sourceUri": "s3://.../landing/violations.csv",
  "summary": "..."
}
```

## Deploy

Prerequisites: AWS creds, `aws` v2, `jq`, and either `docker` (with buildx)
or `finch` for `linux/arm64` builds (AgentCore Runtime requires arm64).
**Deploy `data-profiling-agent` first** — this project piggybacks on its
S3 bucket (default `data-profiling-agent-${ACCOUNT}-${REGION}`) so the EventBridge rule has
something to listen to.

```
./deploy.sh
```

Defaults (override via env): `AWS_REGION=us-east-1`, `PREFIX=data-architect-agent`,
`DATA_BUCKET=data-profiling-agent-${ACCOUNT}-${REGION}`,
`MODEL_ID=us.anthropic.claude-sonnet-4-6`,
`GLUE_DATABASE=data_architect_agent_curated`. The script is idempotent — re-runs update
everything in place.

The deploy creates: five IAM roles (lambda, bridge, glue, gateway,
runtime), a Cognito user pool / resource server / app client / test user,
a Secrets Manager secret, the tools Lambda, an AgentCore Gateway with a
Lambda target and inline tool schema, an ECR repo + the agent container
image, the AgentCore Runtime, the bridge Lambda, an EventBridge rule that
fires on `s3:ObjectCreated` under `insights/`, and uploads the persistent
`curate_job.py` PySpark script to `s3://${BUCKET}/glue-scripts/`. The Glue
database and per-dataset Glue jobs are created lazily by the agent.

## Live test

The full chain is `landing/file.csv` → profiling-agent insights →
architect-agent curated Parquet:

```
# (assumes data-profiling-agent already deployed)
aws s3 cp ../data-profiling-agent/sample/violations.csv \
    s3://data-profiling-agent-${ACCOUNT}-${REGION}/landing/violations.csv

# wait ~3-6 minutes (DataBrew profile + insights write + Glue ETL), then:
aws glue get-tables --database-name data_architect_agent_curated --query 'TableList[].Name'
aws s3 ls s3://data-profiling-agent-${ACCOUNT}-${REGION}/curated/
aws s3 cp s3://data-profiling-agent-${ACCOUNT}-${REGION}/designs/violations.json - | jq .
```

You can also bypass EventBridge and invoke the runtime directly with a
known insights URI:

```
RUNTIME_ARN=<from deploy output> SECRET_NAME=data-architect-agent/gateway-credentials \
AWS_REGION=us-east-1 \
  python scripts/invoke_agent.py s3://data-profiling-agent-${ACCOUNT}-${REGION}/insights/violations.json
```

## Layout

```
data-architect-agent/
├── deploy.sh                    # one-shot, idempotent
├── teardown.sh
├── tools/lambda_function.py     # MCP tools target
├── glue/curate_job.py           # persistent PySpark ETL (raw -> Parquet)
├── agent/
│   ├── data_architect_agent.py  # Strands @tool wrappers + entrypoint
│   ├── Dockerfile               # python3.13 / arm64 / bedrock-agentcore
│   └── requirements.txt
├── bridge/lambda_function.py    # insights/ -> Cognito token -> Runtime
└── scripts/invoke_agent.py      # manual invocation helper
```

## Teardown

```
./teardown.sh
```

Deletes everything data-architect-agent created — EventBridge rule, both Lambdas, the
AgentCore Runtime, the Gateway and target, the ECR repo, the Glue jobs
matching `data-architect-agent-curate-*`, the `data_architect_agent_curated` database and its tables, the
Cognito pool, the Secrets Manager secret, and all five IAM roles. The
shared S3 bucket is left intact (it belongs to data-profiling-agent); set
`DELETE_DAA_DATA=1` to also empty the `curated/`, `designs/`,
`glue-scripts/`, and `glue-temp/` prefixes.
