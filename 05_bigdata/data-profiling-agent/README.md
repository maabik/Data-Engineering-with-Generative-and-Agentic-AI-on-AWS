# data-profiling-agent

Strands agent on **Bedrock AgentCore Runtime** that profiles every dataset
landing in the data-lake landing zone and emits a structured insights JSON
(schema, primary key candidates, partition candidates, PII, domain).

The deterministic step (Glue DataBrew profile job) and the probabilistic step
(LLM analysis) live behind one MCP-over-HTTP interface exposed via **AgentCore
Gateway**.

```
   S3 PUT under landing/                          Cognito JWT
            │                                          │
            ▼                                          ▼
   EventBridge ──► data-profiling-agent-bridge ─►  AgentCore Runtime  (data_profiling_agent)
                                                       │   Strands agent container
                                                       │
                                              MCP-over-HTTP, JWT
                                                       ▼
                                          AgentCore Gateway (data-profiling-agent-gw)
                                                       │
                                          Lambda invoke (GATEWAY_IAM_ROLE)
                                                       ▼
                                  ┌────────────────────────────────────┐
                                  │  data-profiling-agent-tools        │
                                  │  • profile_dataset                 │
                                  │  • analyze_profiling_report        │
                                  └─────────────────┬──────────────────┘
                                                    │ DataBrew create + run
                                                    │ S3 read profile JSON
                                                    ▼
                                       AWS Glue DataBrew profile job
                                                    │
                                                    ▼
                                       s3://${BUCKET}/profile/<job>/...json
                                                    │  (slim, ~per-column stats)
                                                    ▼
                                       agent synthesises insights
                                                    ▼
                                       s3://${BUCKET}/insights/<file>.json
```

## Why this shape

The book's brief: **"agent profiles a never-before-seen dataset."** A pure
Bedrock InvokeModel pass would only ever see whatever JSON we hand-pick to
shove into the prompt. An agent loop with tools lets the LLM:

1. drive the deterministic profiler (`profile_dataset`),
2. reason about the (potentially 8 000-line) report by re-fetching it as
   needed (`analyze_profiling_report`), and
3. synthesise typed insights into a stable JSON contract that the **next**
   agent in the chain — the Data Architect Agent — can consume without
   re-prompting Bedrock.

Putting the tools behind AgentCore Gateway means:

- The Lambda owns DataBrew + S3 + IAM PassRole; the runtime container only
  needs MCP/Cognito/Bedrock-for-the-LLM.
- New tools are added by editing `tools/lambda_function.py` and the
  `toolSchema` in `deploy.sh` — no agent rebuild.
- Cross-agent reuse: any other Strands or Bedrock agent in the account can
  attach the same Gateway and reuse the same tools with the same auth model.

## Tools

| Tool                        | Input                         | Output                                             |
| --------------------------- | ----------------------------- | -------------------------------------------------- |
| `profile_dataset`           | `s3_uri`, `timeout_seconds?`  | `{ok, job, runId, state, input, reportUri}`        |
| `analyze_profiling_report`  | `report_uri`                  | `{ok, reportUri, report:{sampleSize, columns,…}}`  |

`profile_dataset` is the slow one: it creates / updates a DataBrew dataset +
FULL_DATASET profile job for the new S3 object, starts an asynchronous run,
and polls until SUCCEEDED / FAILED / TIMEOUT.

`analyze_profiling_report` is the cheap one: it fetches the profile JSON from
S3 and strips per-column `valueDistribution` histograms (which can dominate
the file at 100s of KB per column) before returning, so the agent can reason
about column statistics without blowing its context window.

## Insights contract

The agent's final output (and the object written to `s3://${BUCKET}/insights/<file>.json`)
is a single JSON object with these keys:

```json
{
  "tableName": "...",
  "sourceUri": "s3://...",
  "reportUri": "s3://.../profile/.../*.json",
  "sampleSize": 20,
  "schema": [{ "column": "...", "dataType": "...", "nullPct": 0, ... }],
  "primaryKeyCandidates": ["..."],
  "partitionCandidates": ["..."],
  "indexCandidates": ["..."],
  "sensitivity": [{ "column": "...", "type": "PII", "reason": "..." }],
  "dataDomain": "...",
  "summary": "..."
}
```

## Deploy

Prerequisites: AWS creds, `aws` v2, `jq`, and either `docker` (with buildx)
or `finch` for `linux/arm64` builds (AgentCore Runtime requires arm64).

```
./deploy.sh
```

Defaults (override via env): `AWS_REGION=us-east-1`, `PREFIX=data-profiling-agent`,
`MODEL_ID=us.anthropic.claude-sonnet-4-6`. The script is idempotent — re-runs
update everything in place.

The deploy creates: an S3 bucket with EventBridge enabled, five IAM roles, a
Cognito user pool / resource server / app client / test user, a Secrets
Manager secret, the tools Lambda, an AgentCore Gateway with a Lambda target
and inline tool schema, an ECR repo + the agent container image, the
AgentCore Runtime, the bridge Lambda, and an EventBridge rule that fires on
`s3:ObjectCreated` under `landing/`.

## Live test

```
aws s3 cp sample/violations.csv s3://data-profiling-agent-${ACCOUNT}-${REGION}/landing/violations.csv

# wait ~2-3 minutes for the DataBrew job + agent loop, then:
aws s3 cp s3://data-profiling-agent-${ACCOUNT}-${REGION}/insights/violations.json - | jq .
```

Excerpt from a real run on the bundled `sample/violations.csv` (20 rows,
9 columns):

```jsonc
{
  "tableName": "violations",
  "sampleSize": 20,
  "primaryKeyCandidates": ["ID", "DOCKET NUMBER"],
  "partitionCandidates":  ["VIOLATION DATE", "DISPOSITION"],
  "indexCandidates":      ["DOCKET NUMBER", "ID", "RESPONDENTS"],
  "sensitivity": [
    { "column": "ADDRESS",     "type": "PII", "reason": "..." },
    { "column": "RESPONDENTS", "type": "PII", "reason": "..." }
  ],
  "dataDomain": "Regulatory Violations & Enforcement",
  "summary": "...DISPOSITION is a binary low-cardinality enum that makes
              an excellent partition key alongside VIOLATION DATE.
              The FINE column is right-skewed (skewness≈1.60) with a mode
              of $0... Two columns — ADDRESS and RESPONDENTS — carry PII..."
}
```

You can also bypass EventBridge and invoke the runtime directly:

```
RUNTIME_ARN=<from deploy output> SECRET_NAME=data-profiling-agent/gateway-credentials \
AWS_REGION=us-east-1 \
  python scripts/invoke_agent.py s3://data-profiling-agent-${ACCOUNT}-${REGION}/landing/violations.csv
```

## Layout

```
data-profiling-agent/
├── deploy.sh               # one-shot, idempotent
├── teardown.sh
├── tools/lambda_function.py    # MCP tools target
├── agent/
│   ├── data_profiling_agent.py # Strands @tool wrappers + entrypoint
│   ├── Dockerfile              # python3.13 / arm64 / bedrock-agentcore
│   └── requirements.txt
├── bridge/lambda_function.py   # S3 -> Cognito token -> AgentCore Runtime
├── scripts/invoke_agent.py     # manual invocation helper
└── sample/violations.csv       # demo dataset (Chicago violations shape)
```

## Teardown

```
./teardown.sh
```

Deletes everything the project created — EventBridge rule, both Lambdas,
the AgentCore Runtime, the Gateway and target, the ECR repo, the DataBrew
jobs / datasets it spawned, the S3 bucket (emptied first), the Cognito pool,
the Secrets Manager secret, and all five IAM roles.
