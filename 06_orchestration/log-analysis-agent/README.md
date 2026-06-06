# log-analysis-agent

Strands agent on **Bedrock AgentCore Runtime** that consumes a
pipeline-run descriptor (Airflow DAG + Spark application) the moment
the pipeline finishes, reads the corresponding Airflow scheduler/task
logs and Spark driver/executor stderr, decides what happened, writes
a structured insights JSON to S3, and publishes a one-line headline
to SNS.

The six MCP tools (`read_pipeline_run`, `read_airflow_logs`,
`read_spark_logs`, `list_pipeline_history`, `write_log_insights`,
`send_sns_notification`) live behind one Lambda exposed via
**AgentCore Gateway**.

```
   orchestrator writes                                Cognito JWT
   s3://.../pipeline-runs/<run_id>.json                       │
            │                                                 ▼
            ▼                                          AgentCore Runtime  (log_analysis_agent)
   EventBridge ──► log-analysis-agent-bridge ────►    Strands agent container
                                                              │
                                                      MCP-over-HTTP, JWT
                                                              ▼
                                               AgentCore Gateway (log-analysis-agent-gw)
                                                              │
                                               Lambda invoke (GATEWAY_IAM_ROLE)
                                                              ▼
                                ┌─────────────────────────────────────────┐
                                │  log-analysis-agent-tools (Lambda)      │
                                │  • read_pipeline_run                    │
                                │  • read_airflow_logs   (S3 or CWL)      │
                                │  • read_spark_logs     (S3 or CWL)      │
                                │  • list_pipeline_history                │
                                │  • write_log_insights                   │
                                │  • send_sns_notification                │
                                └─────────────────┬───────────────────────┘
                                                  │
                                                  │  reads logs from S3/CloudWatch
                                                  │  + writes insights JSON to
                                                  │  s3://${BUCKET}/log-insights/
                                                  │  + sns:Publish to log-analysis-agent-pipeline-alerts
                                                  ▼
                              agent emits insights JSON
                              s3://${BUCKET}/log-insights/<run_id>.json
                              SNS subject: "[<verdict>] <pipeline> run <run_id>"
```

## Why an agent here

A typical Airflow + Spark failure produces three artifacts: an Airflow
task log, a Spark driver stderr, and zero context. The on-call
engineer's first 10 minutes are spent finding those files, eyeballing
them, and translating Spark's stack traces into a one-paragraph
verdict. This agent automates exactly those 10 minutes:

1. **What happened?** Tail the Airflow log (final task state, retry
   count, the first ERROR/Traceback) and the Spark driver stderr
   (exception class, OOM signals, executor-lost messages, the first
   few stack frames). Quote the actual log lines as `evidence` so the
   summary is verifiable.
2. **Is this regression or chronic?** S3-list the most-recent
   `pipeline-runs/<pipeline>*` records to compute a rough failure
   rate over the last 10 runs.
3. **What should the on-call do?** Emit a structured insights JSON
   (`rootCauses`, ordered `recommendations`, `timeline`, `history`,
   `summary`) and publish a 1-line SNS headline with the insightsUri
   so on-call can pull the full plan from S3.

The agent has read-only access to logs (S3 + CloudWatch Logs) and
write-only access to one S3 prefix and one SNS topic — its blast
radius is exactly the insights it produces.

## Tools

| Tool                       | Input                                                          | Output                                                                            |
| -------------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `read_pipeline_run`        | `run_uri`                                                      | `{ok, runUri, run:{run_id, pipeline, status, started_at, ended_at, airflow, spark}}` |
| `read_airflow_logs`        | `source, max_lines?`                                           | `{ok, source:{...}, lineCount, logs}` (most-recent N lines)                       |
| `read_spark_logs`          | `source, max_lines?`                                           | `{ok, source:{...}, lineCount, logs}` (most-recent N lines)                       |
| `list_pipeline_history`    | `pipeline, limit?`                                             | `{ok, pipeline, rowCount, rows:[{key,lastModified,size}]}`                        |
| `write_log_insights`       | `run_uri, insights`                                            | `{ok, insightsUri}`                                                               |
| `send_sns_notification`    | `subject, message`                                             | `{ok, messageId, topicArn}`                                                       |

`source` is one of:
- `{"s3_uri": "s3://bucket/path/to/file_or_prefix"}` — supports `.gz`
  transparently, and a key ending in `/` concatenates every object's
  content under that prefix in sorted order (matches the EMR
  Serverless `applications/.../SPARK_DRIVER/stderr.gz` layout).
- `{"log_group": "/aws/airflow/...", "log_stream": "<task_log_stream>"}`
  — when `log_stream` is omitted, falls back to `FilterLogEvents`
  across the whole group.

`max_lines` is capped at 1000 to keep the Lambda payload below the
Bedrock Converse limit.

## Pipeline-run descriptor contract

The trigger object the orchestrator writes to
`s3://${BUCKET}/pipeline-runs/<run_id>.json`:

```json
{
  "run_id": "sales_etl_20260520_021500",
  "pipeline": "sales_etl",
  "status": "Failed",
  "started_at": "2026-05-20T02:15:00Z",
  "ended_at": "2026-05-20T02:38:42Z",
  "airflow": {
    "dag_id": "sales_etl",
    "task_id": "transform_orders_spark",
    "try_number": 2,
    "source": { "s3_uri": "s3://.../airflow/sales_etl_20260520.log" }
  },
  "spark": {
    "application_id": "application_1747700100200_0042",
    "master": "EMR Serverless",
    "source": { "s3_uri": "s3://.../spark/sales_etl_20260520_stderr.log" }
  }
}
```

`status` is freeform (`Success`, `Failed`, `Error`, `Timeout`); the
agent uses it as a hint, not a verdict. `airflow.source` and
`spark.source` are the only required fields the agent dereferences;
everything else is metadata for the human report.

## Insights contract

The agent's final output (and the object written to
`s3://${BUCKET}/log-insights/<run_id>.json`) is a single JSON object:

```json
{
  "run_id": "sales_etl_20260520_021500",
  "pipeline": "sales_etl",
  "status": "Failed",
  "verdict": "broken",
  "severity": "high",
  "rootCauses": [
    {
      "id": "spark-oom-executor",
      "component": "spark",
      "hypothesis": "Executor 6 was killed by YARN for exceeding 10GB physical memory while sorting shuffle data; the resulting Task 17 retry storm aborted stage 4.",
      "evidence": [
        "Container killed by YARN for exceeding memory limits.  10.4 GB of 10 GB physical memory used.",
        "java.lang.OutOfMemoryError: Java heap space"
      ],
      "confidence": "high"
    }
  ],
  "timeline": [
    { "ts": "2026-05-20T02:15:01Z", "component": "airflow", "event": "transform_orders_spark started (try 2)" },
    { "ts": "2026-05-20T02:19:40Z", "component": "spark",   "event": "stage 4 task 17 failed 4 times — ExecutorLostFailure" },
    { "ts": "2026-05-20T02:32:12Z", "component": "airflow", "event": "task marked FAILED" }
  ],
  "recommendations": [
    {
      "priority": 1,
      "owner": "Data Platform / on-call",
      "action": "Raise spark.executor.memoryOverhead from default to 2GB and re-run.",
      "detail": "Executor 6 OOMed during shuffle sort; an extra 2GB overhead absorbs the spike without changing executor count.",
      "estimatedEffort": "30 min"
    }
  ],
  "history": { "recentRuns": 10, "failureRate": 0.2, "trend": "degrading" },
  "summary": "Sales ETL run failed because the Spark transform OOMed on shuffle sort...",
  "insightsUri": "s3://.../log-insights/sales_etl_20260520_021500.json"
}
```

`verdict` is `ok` (clean run), `investigate` (failed but no clear
root cause in the captured log window), or `broken` (failed with at
least one high-confidence root cause).

## Deploy

Prerequisites: AWS creds, `aws` v2, `jq`, and either `docker` (with
buildx) or `finch` for `linux/arm64` builds (AgentCore Runtime
requires arm64). The shared data lake bucket
(default `data-profiling-agent-${ACCOUNT}-${REGION}`) is created if missing.

```
./deploy.sh
```

Defaults (override via env): `AWS_REGION=us-east-1`, `PREFIX=log-analysis-agent`,
`DATA_BUCKET=data-profiling-agent-${ACCOUNT}-${REGION}`,
`MODEL_ID=us.anthropic.claude-sonnet-4-6`,
`RUN_PREFIX=pipeline-runs/`,
`INSIGHTS_PREFIX=log-insights/`,
`SNS_TOPIC_NAME=log-analysis-agent-pipeline-alerts`. Idempotent — re-runs update
everything in place.

The deploy creates: an SNS topic, four IAM roles (lambda, bridge,
gateway, runtime), a Cognito user pool / resource server / app client
/ test user, a Secrets Manager secret, the tools Lambda, an
AgentCore Gateway with a Lambda target and the inline tool schema,
an ECR repo + the agent container image, the AgentCore Runtime, the
bridge Lambda, and an EventBridge rule that fires on
`s3:ObjectCreated` under `pipeline-runs/`.

## Live test

A complete sample is included in `sample/`. Push the two log files
into the bucket, then drop a run descriptor that points at them:

```
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET=data-profiling-agent-${ACCOUNT}-us-west-2

aws s3 cp sample/sales_etl_20260520.log         s3://${BUCKET}/sample-logs/airflow/
aws s3 cp sample/sales_etl_20260520_stderr.log  s3://${BUCKET}/sample-logs/spark/

# rewrite __BUCKET__ -> the actual bucket name and drop the run descriptor
sed "s/__BUCKET__/${BUCKET}/g" sample/sample-run.json \
  | aws s3 cp - s3://${BUCKET}/pipeline-runs/sales_etl_20260520_021500.json

# wait ~60-120s, then read the agent's insights:
aws s3 cp s3://${BUCKET}/log-insights/sales_etl_20260520_021500.json - | jq .
```

You can also bypass EventBridge and invoke the runtime directly:

```
RUNTIME_ARN=<from deploy output> SECRET_NAME=log-analysis-agent/gateway-credentials \
AWS_REGION=us-east-1 \
  python scripts/invoke_agent.py s3://${BUCKET}/pipeline-runs/sales_etl_20260520_021500.json
```

To receive the SNS notification, subscribe an endpoint after deploy:

```
aws sns subscribe \
  --topic-arn arn:aws:sns:us-west-2:${ACCOUNT}:log-analysis-agent-pipeline-alerts \
  --protocol email --notification-endpoint you@example.com \
  --region us-west-2
```

## Layout

```
log-analysis-agent/
├── deploy.sh                      # one-shot, idempotent
├── teardown.sh
├── tools/lambda_function.py       # MCP tools target
├── agent/
│   ├── log_analysis_agent.py      # Strands @tool wrappers + entrypoint
│   ├── Dockerfile                 # python3.13 / arm64 / bedrock-agentcore
│   └── requirements.txt
├── bridge/lambda_function.py      # pipeline-runs/ -> Cognito token -> Runtime
├── scripts/invoke_agent.py        # manual invocation helper
└── sample/                        # sample run descriptor + Airflow + Spark logs
```

## Teardown

```
./teardown.sh
```

Deletes everything log-analysis-agent created — EventBridge rule, both Lambdas, the
AgentCore Runtime, the Gateway and target, the ECR repo, the Cognito
pool, the Secrets Manager secret, the SNS topic, and all four IAM
roles. The shared S3 bucket is left intact; set `DELETE_LAA_DATA=1`
to also empty the `pipeline-runs/` and `log-insights/` prefixes.
