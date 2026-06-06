# data-quality-agent

Strands agent on **Bedrock AgentCore Runtime** that consumes the
[`data-quality-deequ-agent`](../data-quality-deequ-agent/) insights JSON,
investigates the cause of any failed Deequ constraints, and writes an
actionable **remediation plan** a data engineer can pick up and execute.

The six MCP tools (`read_dq_insights`, `read_dq_results`, `read_design`,
`query_dq_history`, `peek_failing_rows`, `write_remediation_plan`) live
behind one Lambda exposed via **AgentCore Gateway**.

```
   data-quality-deequ-agent writes                                Cognito JWT
   s3://.../dq-insights/<file>.json                       │
            │                                             ▼
            ▼                                      AgentCore Runtime  (data_quality_agent)
   EventBridge ──► data-quality-agent-bridge ────►       Strands agent container
                                                          │
                                                  MCP-over-HTTP, JWT
                                                          ▼
                                           AgentCore Gateway (data-quality-agent-gw)
                                                          │
                                           Lambda invoke (GATEWAY_IAM_ROLE)
                                                          ▼
                                ┌─────────────────────────────────────────┐
                                │  data-quality-agent-tools (Lambda)                     │
                                │  • read_dq_insights                     │
                                │  • read_dq_results                      │
                                │  • read_design                          │
                                │  • query_dq_history                     │
                                │  • peek_failing_rows                    │
                                │  • write_remediation_plan               │
                                └─────────────────┬───────────────────────┘
                                                  │  S3 read on
                                                  │  dq-insights/, dq-reports/, designs/
                                                  │  + Athena round-trip on
                                                  │  data_quality_deequ_agent_dq.dq_results
                                                  │  + read on
                                                  │  data_architect_agent_curated.<curated_table>
                                                  ▼
                              agent emits remediation plan JSON
                              s3://${BUCKET}/dq-remediation/<file>.json
```

## Why a third agent

The data-quality-deequ-agent's job ends with a **verdict**: which Deequ constraints
passed, which failed, and a one-paragraph summary. That is sufficient to
gate downstream consumers but it does *not* tell the on-call data
engineer what to do next. This agent answers three follow-up questions
without a human in the loop:

1. **What actually broke?** Read the full per-constraint Deequ results,
   correlate the failing checks with the design's column types and
   partitions, and (optionally) `SELECT *` a handful of failing rows so
   the hypothesis is grounded in real data.
2. **Is this regression or chronic?** Query the `data_quality_deequ_agent_dq.dq_results`
   Athena table for the last N runs of this curated table and report the
   trend (improving / stable / degrading), so the engineer knows whether
   to roll back the most recent ETL change or treat it as a known issue.
3. **What's the fix?** Emit a structured remediation plan (`rootCauses`,
   ordered `remediationSteps` with priority/owner/effort, `reRunGuidance`)
   that's machine-readable and reviewable in S3.

The agent does **not** own a Glue job or its own Athena workgroup — it
re-uses the data-quality-deequ-agent's `data-quality-deequ-agent-wg` workgroup and reads its
`data_quality_deequ_agent_dq.dq_results` table read-only via Lake Formation grants on
`TableWildcard`. Lower blast radius, no duplicate infra.

## Tools

| Tool                       | Input                                                          | Output                                                                            |
| -------------------------- | -------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `read_dq_insights`         | `insights_uri`                                                 | `{ok, insightsUri, insights:{...slim summary...}}`                                |
| `read_dq_results`          | `results_uri`                                                  | `{ok, resultsUri, results:{checks[], completeness}}`                              |
| `read_design`              | `design_uri`                                                   | `{ok, designUri, design:{columns, partitions, location, ...}}`                    |
| `query_dq_history`         | `table, limit?`                                                | `{ok, columns, rows:[{run_id,total,passed,failed,last_ts}], rowCount}`            |
| `peek_failing_rows`        | `database, table, predicate, limit?`                           | `{ok, columns, rows[], rowCount}`                                                 |
| `write_remediation_plan`   | `insights_uri, plan`                                           | `{ok, planUri}`                                                                   |

`peek_failing_rows` rejects predicates containing `;`, `--`, or `/*` — the
predicate is LLM-generated free-form text, the guard keeps `SELECT *`
single-statement.

`query_dq_history` enumerates `run_id=<…>` partitions from S3 to seed an
`IN (…)` clause: the data-quality-deequ-agent registers `dq_results` with Athena
*injected* partition projection on `run_id`, which requires a static
equality filter. The S3 listing supplies that filter.

## Remediation contract

The agent's final output (and the object written to
`s3://${BUCKET}/dq-remediation/<file>.json`) is a single JSON object:

```json
{
  "database": "data_architect_agent_curated",
  "table": "violations_curated",
  "runId": "violations_curated_20250522",
  "verdict": "ready",
  "severity": "info",
  "rootCauses": [],
  "remediationSteps": [
    {
      "priority": 1,
      "owner": "Data Governance / Security Team",
      "action": "Confirm PII access controls and masking ...",
      "detail": "...",
      "estimatedEffort": "2-4 hours"
    }
  ],
  "reRunGuidance": "No re-run is required ...",
  "history": {
    "trend": "stable",
    "recentRuns": [
      { "run_id": "violations_curated_20250522", "total": 9, "passed": 9,
        "failed": 0, "last_ts": "2026-05-19T18:42:13Z" }
    ]
  },
  "summary": "...",
  "planUri": "s3://.../dq-remediation/violations.json"
}
```

`verdict` is `ready` (clean run), `remediable` (failures with a known
fix), or `blocked` (the run itself failed upstream and the engineer
needs to investigate before re-running).

## Deploy

Prerequisites: AWS creds, `aws` v2, `jq`, and either `docker` (with
buildx) or `finch` for `linux/arm64` builds (AgentCore Runtime requires
arm64). **Deploy `data-profiling-agent`, `data-architect-agent`, and
`data-quality-deequ-agent` first** — this project piggybacks on the
shared S3 bucket (default `data-profiling-agent-${ACCOUNT}-${REGION}`), reuses the
`data-quality-deequ-agent-wg` Athena workgroup, reads `data_quality_deequ_agent_dq.dq_results`, and reads
`data_architect_agent_curated.<curated_table>` for `peek_failing_rows`.

```
./deploy.sh
```

Defaults (override via env): `AWS_REGION=us-east-1`, `PREFIX=data-quality-agent`,
`DATA_BUCKET=data-profiling-agent-${ACCOUNT}-${REGION}`,
`MODEL_ID=us.anthropic.claude-sonnet-4-6`, `DQ_DATABASE=data_quality_deequ_agent_dq`,
`DQ_TABLE=dq_results`, `DAA_DATABASE=data_architect_agent_curated`,
`ATHENA_WORKGROUP=data-quality-deequ-agent-wg`. The deploy fails fast if either the data
bucket or the `data-quality-deequ-agent-wg` workgroup is missing. Idempotent — re-runs
update everything in place.

The deploy creates: four IAM roles (lambda, bridge, gateway, runtime), a
Cognito user pool / resource server / app client / test user, a Secrets
Manager secret, the tools Lambda, an AgentCore Gateway with a Lambda
target and the inline tool schema, an ECR repo + the agent container
image, the AgentCore Runtime, the bridge Lambda, an EventBridge rule
that fires on `s3:ObjectCreated` under `dq-insights/`, and Lake
Formation `DESCRIBE` + `SELECT` grants on `data_quality_deequ_agent_dq` and `data_architect_agent_curated` to
the Lambda role.

## Live test

End-to-end chain: `landing/file.csv` → profiling-agent insights →
architect-agent design + curated Parquet → data-quality-deequ-agent quality report →
**data-quality-agent remediation plan**:

```
# (assumes the three upstream agents already deployed)
aws s3 cp ../data-profiling-agent/sample/violations.csv \
    s3://data-profiling-agent-${ACCOUNT}-${REGION}/landing/violations.csv

# wait ~7-12 minutes for the full chain, then:
aws s3 cp s3://data-profiling-agent-${ACCOUNT}-${REGION}/dq-remediation/violations.json - | jq .
```

You can also bypass EventBridge and invoke the runtime directly with a
known dq-insights URI:

```
RUNTIME_ARN=<from deploy output> SECRET_NAME=data-quality-agent/gateway-credentials \
AWS_REGION=us-east-1 \
  python scripts/invoke_agent.py s3://data-profiling-agent-${ACCOUNT}-${REGION}/dq-insights/violations.json
```

## Layout

```
data-quality-agent/
├── deploy.sh                       # one-shot, idempotent
├── teardown.sh
├── tools/lambda_function.py        # MCP tools target
├── agent/
│   ├── data_quality_agent.py       # Strands @tool wrappers + entrypoint
│   ├── Dockerfile                  # python3.13 / arm64 / bedrock-agentcore
│   └── requirements.txt
├── bridge/lambda_function.py       # dq-insights/ -> Cognito token -> Runtime
└── scripts/invoke_agent.py         # manual invocation helper
```

## Teardown

```
./teardown.sh
```

Deletes everything data-quality-agent created — EventBridge rule, both Lambdas, the
AgentCore Runtime, the Gateway and target, the ECR repo, the Cognito
pool, the Secrets Manager secret, the Lake Formation grants on
`data_quality_deequ_agent_dq` and `data_architect_agent_curated`, and all four IAM roles. The shared S3
bucket, the `data-quality-deequ-agent-wg` workgroup, and the data-quality-deequ-agent / data-architect-agent Glue databases are
left intact; set `DELETE_DQR_DATA=1` to also empty the
`dq-remediation/` prefix.
