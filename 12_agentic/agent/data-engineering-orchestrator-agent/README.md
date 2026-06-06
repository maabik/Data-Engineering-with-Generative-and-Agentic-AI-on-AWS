# Data Engineering Orchestrator Agent

A Strands agent on **Amazon Bedrock AgentCore Runtime** that receives every
data-engineering request to this organization and routes it to the right
specialist agent shipped in this repo. Single-shot questions go to one
agent. Multi-step pipelines (profile → design → quality → remediation)
chain agents in sequence. Independent questions fan out in parallel.

## What it routes to

The orchestrator's downstream registry is built **at deploy time** by
listing AgentCore Runtimes in `us-east-1` and `us-west-2` and matching
well-known runtime names from the repo. As of this writing it covers all
ten qualifying agents:

| Agent runtime              | Region    | Source                                         | What it does |
|----------------------------|-----------|------------------------------------------------|--------------|
| `data_profiling_agent`     | us-west-2 | `05_bigdata/data-profiling-agent`              | Profiles a landing-zone dataset (DataBrew); emits insights JSON. |
| `data_architect_agent`     | us-west-2 | `05_bigdata/data-architect-agent`              | Designs schema, registers Glue tables, runs PySpark ETL to curated Parquet. |
| `data_quality_deequ_agent` | us-west-2 | `05_bigdata/data-quality-deequ-agent`          | Runs Amazon Deequ constraint suggestion + verification. |
| `data_quality_agent`       | us-west-2 | `05_bigdata/data-quality-agent`                | Investigates failed Deequ constraints, writes a remediation plan. |
| `api_integration_agent`    | us-west-2 | `05_bigdata/api-integration-agent`             | Generates ingestion Lambda from an API spec, deploys it, smoke-tests. |
| `log_analysis_agent`       | us-west-2 | `06_orchestration/log-analysis-agent`          | Root-causes Airflow/Spark pipeline failures from logs. |
| `text_to_dag_agent`        | us-west-2 | `06_orchestration/text-to-dag-agent`           | Turns a natural-language pipeline into Step Functions ASL or an Airflow DAG. |
| `sql_evaluations_agent`    | us-east-1 | `12_agentic/agent/sql-evaluations-agent`       | Reviews SQL for errors, optimization, and security. |
| `sales_reporting_agent`    | us-east-1 | `12_agentic/agent/sales-reporting-agent`       | Answers sales questions against the chapter-10 Redshift schema. |
| `redshift_mcp_server_agentcore` | us-east-1 | `12_agentic/mcp/02-hosting-MCP-server` | Redshift MCP tools (introspection + execute_redshift_query) on AgentCore Runtime. Surfaced as a capability, not an HTTP-JSON callee. |

The deploy script writes the discovered registry into the runtime's
`AGENT_REGISTRY_JSON` environment variable and scopes the runtime IAM
role's `bedrock-agentcore:InvokeAgentRuntime` permission to just those
ARNs (across both regions).

## Architecture

```
                         ┌──────────────────────────────┐
   user / app  ───────►  │  AgentCore Runtime           │
                         │  data_engineering_orchestrator_agent  (us-east-1)      │
                         │  Strands + Bedrock Sonnet 4.6│
                         └──────────────┬───────────────┘
                                        │ invoke_agent_runtime (boto3)
                                        ▼
                ┌────────────────────────────────────────────────┐
                │   10 specialist AgentCore Runtimes             │
                │   (us-east-1 and us-west-2)                    │
                │   data_profiling  data_architect              │
                │   data_quality_deequ  data_quality            │
                │   api_integration                              │
                │   log_analysis  text_to_dag                    │
                │   sql_evaluations  sales_reporting  redshift_mcp │
                └────────────────────────────────────────────────┘
```

Tools exposed to the LLM:

- `list_agents()` — full registry with descriptions and payload hints.
- `invoke_agent(name, payload)` — call one specialist.
- `invoke_agents_parallel([{name, payload}, …])` — fan out concurrently.

## What it returns

The orchestrator always returns a single JSON object:

```json
{
  "plan": "1-3 sentence routing decision",
  "agents_invoked": ["sql_evaluations_agent", "sales_reporting_agent"],
  "results": {
    "sql_evaluations_agent":             { ... that agent's response ... },
    "sales_reporting_agent": { ... that agent's response ... }
  },
  "summary": "user-facing answer that synthesizes the agent results"
}
```

## Deploy

Prerequisites:
- AWS credentials (default profile)
- Region `us-east-1`
- `docker buildx` or `finch` for the ARM64 image build
- The specialist agents this orchestrator routes to are already deployed
  (it is fine if some are missing — the registry just shrinks).

```bash
bash deploy.sh
```

Single-shot, idempotent. Provisions:

| Resource           | Name                              |
|--------------------|-----------------------------------|
| IAM role           | `data-engineering-orchestrator-agent-runtime-role`                |
| ECR repo           | `data-engineering-orchestrator-agent` (linux/arm64)         |
| AgentCore Runtime  | `data_engineering_orchestrator_agent`                       |
| SSM parameter      | `/data-engineering-orchestrator-agent/agent_runtime_arn`          |

The runtime role is scoped to `bedrock-agentcore:InvokeAgentRuntime` on
exactly the downstream ARNs discovered at deploy time (across both
regions). Re-run `deploy.sh` after deploying any new specialist agent so
the orchestrator picks it up.

## Invoke

```bash
AGENT_RUNTIME_ARN=$(aws ssm get-parameter --name /data-engineering-orchestrator-agent/agent_runtime_arn \
  --query Parameter.Value --output text)

AGENT_RUNTIME_ARN="$AGENT_RUNTIME_ARN" \
  python3 scripts/invoke_runtime.py "Profile s3://data-profiling-agent-123456789012-us-west-2/landing/orders.csv"
```

Other example prompts the orchestrator handles:

```text
"Review this SQL: SELECT * FROM users WHERE name LIKE '%a%'"
"Run a full medallion pipeline on s3://data-profiling-agent-…/landing/orders.csv:
   profile it, design it, run quality, and remediate any failures."
"What were total Q3 sales by region?"
"Convert this description to a Step Functions DAG: every hour,
   pull the latest orders from the API, validate, then load to Redshift."
"Why did pipeline run pipeline-runs/run-2024-09-30T01.json fail?"
```

## Project layout

```
data-engineering-orchestrator-agent/
├── agent/
│   ├── data_engineering_orchestrator_agent.py  # Strands agent + tools
│   ├── requirements.txt
│   └── Dockerfile                              # python:3.13-slim, ARM64
├── scripts/
│   └── invoke_runtime.py                       # direct boto3 invoke
├── tests/
│   ├── test_routing.py                         # 10-agent routing test suite
│   ├── results.md                              # latest results table
│   └── results.json                            # full raw orchestrator responses
├── deploy.sh                                   # one-shot deploy
└── teardown.sh                                 # one-shot teardown
```

## Tests

`tests/test_routing.py` sends one targeted prompt for each of the 10
downstream specialist agents and asserts that the orchestrator routes to
the expected agent. Run it with:

```bash
AWS_REGION=us-east-1 python3 tests/test_routing.py
```

Each test produces a row in `tests/results.md`:

| Column | Meaning |
|--------|---------|
| **Test** | Friendly name for the scenario being routed. |
| **Expected agent** | Specialist the orchestrator should pick (or `(none — capability)` for the MCP-server case where invocation is not appropriate). |
| **Routed to** | What appeared in `agents_invoked` in the orchestrator's response. |
| **Routing** | ✅ if the orchestrator picked correctly. |
| **Downstream status** | What the specialist actually returned (or the error it raised). Many specialists need real infrastructure to do their work — auth-mismatch errors against us-west-2 agents using Cognito JWT are expected from a SigV4-only caller. |
| **Time (s)** | Wall-clock for the orchestrator round-trip including the downstream call. |
| **Needs data?** | `yes` if the test would only fully succeed end-to-end with real upstream data (S3 landing zones, Redshift cluster, etc.). |

Latest run: **10 / 10** routing pass — see [`tests/results.md`](tests/results.md).

## Teardown

```bash
bash teardown.sh
```

Deletes only the orchestrator's own runtime, ECR repo, IAM role, and SSM
parameter. Downstream specialist agents are untouched.
