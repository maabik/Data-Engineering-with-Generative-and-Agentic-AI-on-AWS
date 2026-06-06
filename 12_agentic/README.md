# 12 — Agentic AI on AWS

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

This chapter contains five deployable sub-projects on
**Amazon Bedrock AgentCore Runtime** (and AgentCore Gateway for MCP):

- [`agent/data-engineering-orchestrator-agent/`](agent/data-engineering-orchestrator-agent/)
  — the **organization-wide entry point**. A Strands orchestrator that
  receives every data-engineering request and routes it to the right
  specialist agent shipped across chapters 05, 06, and 12 (10 agents
  total). Single-shot questions go to one agent; multi-step pipelines
  chain agents in sequence; independent questions fan out in parallel.
- [`agent/sales-reporting-agent/`](agent/sales-reporting-agent/) —
  a [Strands](https://strandsagents.com/) agent that authenticates
  against the Redshift MCP server (via Cognito) and turns natural-language
  sales questions into Redshift queries. Routes SQL through the SQL
  Evaluations Agent before execution.
- [`agent/sql-evaluations-agent/`](agent/sql-evaluations-agent/) — a
  separation-of-concerns SQL reviewer hosted on AgentCore Runtime **and**
  exposed as an MCP tool through AgentCore Gateway.
- [`agent/online-agent-evaluation/`](agent/online-agent-evaluation/) —
  production monitoring that **discovers** every agent in the account
  (AgentCore Runtimes + classic Bedrock Agents in `us-east-1` and
  `us-west-2`) and attaches a sampled LLM-as-judge evaluator to each
  one. Verdicts land as CloudWatch metrics, structured logs, and JSONL
  in S3 — agents themselves do not call the evaluator, so production
  latency is unaffected.
- [`mcp/02-hosting-MCP-server/`](mcp/02-hosting-MCP-server/) — a Model
  Context Protocol (MCP) server that wraps the
  [`awslabs/redshift-mcp-server`](https://github.com/awslabs/mcp) tools
  and is hosted on AgentCore Runtime. Exposes
  `execute_redshift_query` and related introspection tools to any MCP
  client.

The orchestrator sits in front of all of them.

## The orchestrator

The Data Engineering Orchestrator Agent is the single front door to the
ten AgentCore Runtime agents shipped in this repo:

| Runtime                          | Region    | Source                                         |
|----------------------------------|-----------|------------------------------------------------|
| `data_profiling_agent`           | us-west-2 | `05_bigdata/data-profiling-agent`              |
| `data_architect_agent`           | us-west-2 | `05_bigdata/data-architect-agent`              |
| `data_quality_deequ_agent`       | us-west-2 | `05_bigdata/data-quality-deequ-agent`          |
| `data_quality_agent`             | us-west-2 | `05_bigdata/data-quality-agent`                |
| `api_integration_agent`          | us-west-2 | `05_bigdata/api-integration-agent`             |
| `log_analysis_agent`             | us-west-2 | `06_orchestration/log-analysis-agent`          |
| `text_to_dag_agent`              | us-west-2 | `06_orchestration/text-to-dag-agent`           |
| `sql_evaluations_agent`          | us-east-1 | `12_agentic/agent/sql-evaluations-agent`       |
| `sales_reporting_agent`          | us-east-1 | `12_agentic/agent/sales-reporting-agent`       |
| `redshift_mcp_server_agentcore`  | us-east-1 | `12_agentic/mcp/02-hosting-MCP-server`         |

The orchestrator's downstream registry is built **at deploy time** by
listing AgentCore Runtimes in both regions and matching well-known
runtime names. The runtime IAM role is scoped to
`bedrock-agentcore:InvokeAgentRuntime` on exactly those ARNs. Re-run
`deploy.sh` after deploying any new specialist agent so the orchestrator
picks it up.

See
[`agent/data-engineering-orchestrator-agent/README.md`](agent/data-engineering-orchestrator-agent/README.md)
for full details, sample prompts, and the project layout.

## Prerequisites

- AWS account with Bedrock model access (`anthropic.claude-sonnet-4-6` —
  the orchestrator's default model)
- A running Redshift cluster with the sales schema from chapter 10
  (only required if you plan to invoke the sales-reporting agent)
- Docker (or Finch) for AgentCore container builds
- Python 3.12+ and `uv` or `pip`
- AWS CLI configured for `us-east-1`

## Deployment order

The orchestrator only requires that any agent it should know about is
deployed to AgentCore Runtime first. Suggested order:

1. **MCP server** (`mcp/02-hosting-MCP-server/`) — see its README.
   Provisions Cognito, the AgentCore Runtime, stores credentials in
   Secrets Manager, and writes the runtime ARN to SSM at
   `/mcp/redshift/agent_arn`.
2. **Specialist agents** — for each chapter agent you want the
   orchestrator to know about:
   - `05_bigdata/{data-profiling,data-architect,data-quality-deequ,data-quality,api-integration}-agent` — `bash deploy.sh`
   - `06_orchestration/{log-analysis-agent,text-to-dag-agent}` — `bash deploy.sh`
   - `12_agentic/agent/sql-evaluations-agent` — `bash deploy.sh`
   - `12_agentic/agent/sales-reporting-agent` — see below
3. **Sales reporting agent** — from `agent/`:
   ```bash
   # Cognito + Secrets Manager bootstrap (set passwords via env vars first)
   export COGNITO_PASSWORD='...' REDSHIFT_PASSWORD='...'
   ./create_sales_agent_execution_role.sh
   ./sales_agent_setup_cognito.sh

   cd sales-reporting-agent
   # Required at deploy time so the agent can attach to its memory resource:
   #   SALES_MEMORY_ID=<your AgentCore memory id>
   agentcore configure -e sales_reporting_agent.py --requirements-file requirements.txt
   agentcore launch
   ```
4. **Orchestrator** — once all the above are `READY`:
   ```bash
   cd agent/data-engineering-orchestrator-agent
   bash deploy.sh
   ```
   Idempotent. Provisions a `data-engineering-orchestrator-agent-runtime-role`, an `data-engineering-orchestrator-agent` ECR repo,
   the `data_engineering_orchestrator_agent` AgentCore Runtime, and `/data-engineering-orchestrator-agent/agent_runtime_arn` in SSM.

### Invoke the orchestrator

```bash
AGENT_RUNTIME_ARN=$(aws ssm get-parameter --name /data-engineering-orchestrator-agent/agent_runtime_arn \
  --query Parameter.Value --output text)

AGENT_RUNTIME_ARN="$AGENT_RUNTIME_ARN" \
  python3 agent/data-engineering-orchestrator-agent/scripts/invoke_runtime.py \
  "Review this SQL: SELECT * FROM users WHERE name LIKE '%a%'"
```

The response is always a single JSON object:

```json
{
  "plan": "1-3 sentence routing decision",
  "agents_invoked": ["sql_evaluations_agent"],
  "results": { "sql_evaluations_agent": { ... that agent's response ... } },
  "summary": "user-facing answer that synthesizes the agent results"
}
```

## Reference material

- [`agent/SCHEMA_DESCRIPTION.md`](agent/SCHEMA_DESCRIPTION.md) — sales star schema reference
- [`agent/sales_agent_spec.md`](agent/sales_agent_spec.md) — design spec for the sales agent
- [`mcp/sample_queries.sql`](mcp/sample_queries.sql) — analytical queries against the sales schema

## Teardown

Each project has its own teardown script:

```bash
bash agent/data-engineering-orchestrator-agent/teardown.sh   # orchestrator only
bash agent/sql-evaluations-agent/teardown.sh
bash agent/online-agent-evaluation/teardown.sh
cd mcp/02-hosting-MCP-server && python teardown_redshift_mcp.py
```

The orchestrator's teardown only deletes its own runtime, ECR repo, IAM
role, and SSM parameter — downstream specialist agents are untouched.

The sales-reporting-agent does not have an automated teardown — delete
the `SalesAgent*` Cognito pool, the agent runtime, and the
`SalesAgentExecutionRole` manually if needed.

## Notes on this chapter's code

- The hosting tutorial notebook
  ([`mcp/02-hosting-MCP-server/hosting_mcp_server.ipynb`](mcp/02-hosting-MCP-server/hosting_mcp_server.ipynb))
  uses a generic `add_numbers`/`multiply_numbers`/`greet_users` MCP server as
  a primer. The actual deployed server lives at
  [`mcp/02-hosting-MCP-server/redshift_mcp_server.py`](mcp/02-hosting-MCP-server/redshift_mcp_server.py).
- AgentCore state files (`.bedrock_agentcore.yaml`), `DEPLOYMENT_SUMMARY.md`,
  and the local `redshift-mcp-config.json` are gitignored — they're written
  at deploy time per AWS account. Copy
  [`mcp/redshift-mcp-config.example.json`](mcp/redshift-mcp-config.example.json)
  to `redshift-mcp-config.json` and fill in your cluster details when working
  locally.
- The Cognito setup scripts read `COGNITO_PASSWORD` (and
  `REDSHIFT_PASSWORD` for the agent variant) from the environment and fail
  fast if either is unset — no hardcoded passwords. The bearer token is no
  longer echoed; fetch it on demand with
  `aws cognito-idp initiate-auth` after the pool is provisioned.
- The sales-reporting agent reads its AgentCore memory resource from
  `SALES_MEMORY_ID`. If the variable is unset or the memory cannot be
  reached, the agent runs without memory rather than failing.
- The orchestrator's IAM role grants `InvokeAgentRuntime` on each
  discovered downstream ARN (and the `/*` wildcard form for the runtime's
  qualifier resource), scoped per-region. It also has
  `ListAgentRuntimes`/`GetAgentRuntime` so the agent could be extended to
  refresh its registry at runtime.

## Default region

All chapter 12 projects default to **us-east-1**. Each `deploy.sh`
honors `AWS_REGION=…` to override; the orchestrator discovers
specialists across whichever region(s) you deployed them to. For
agents that share auth (Cognito) or data (Redshift), keep them in the
same region.

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| agent/sql-evaluations-agent | AgentCore Runtime (1 vCPU + 2 GB) | $0.1080 | $0.1080 | $0.0895/vCPU-hr + $0.00945/GB-hr while serving. |
| agent/sql-evaluations-agent | AgentCore Gateway + Cognito + Lambda + Secrets Manager + ECR | $0.0006 | $0.0050 | Gateway pay-per-call; secret $0.40/month. |
| agent/sales-reporting-agent | AgentCore Runtime | $0.1080 | $0.1080 | Per-second billing while serving. |
| agent/sales-reporting-agent | Cognito + Secrets Manager | $0.0006 | $0.0010 | Cognito free at demo MAU. |
| agent/sales-reporting-agent | (Redshift cluster) | — | — | Reuses chapter-10 cluster at $6.52/hr — not double-counted here. |
| agent/data-engineering-orchestrator-agent | AgentCore Runtime | $0.1080 | $0.1080 | Per-second billing while serving. |
| agent/data-engineering-orchestrator-agent | ECR + SSM + IAM + Bedrock fan-out | $0.0000 | $0.0050 | Bedrock per call to specialists. |
| agent/online-agent-evaluation | Lambda + CloudWatch Logs subscription + S3 + DynamoDB | $0.0001 | $0.0030 | Per-region. Lambda + judge model billed per sampled invocation (default 5%). |
| mcp/02-hosting-MCP-server | AgentCore Runtime (Redshift MCP) | $0.1080 | $0.1080 | Per-second billing while serving. |
| mcp/02-hosting-MCP-server | Cognito + Secrets Manager + ECR | $0.0006 | $0.0010 | Cognito free at demo MAU. |
| **Chapter total (excluding shared Redshift)** | | **~$0.4338** | **~$0.4440** | Four AgentCore Runtimes + auxiliaries. |
| **Chapter total (with chapter-10 Redshift)** | | **~$6.9538** | **~$6.9640** | Add $6.52/hr if the cluster is up. |

The standing cost is **four AgentCore Runtimes** at $0.108/hr each.
Tear down any agent you aren't using:

```bash
cd 12_agentic/agent/<agent-folder> && bash teardown.sh
cd 12_agentic/mcp/02-hosting-MCP-server && python3 teardown_redshift_mcp.py
```

If you're running the full multi-agent topology (this chapter +
chapters 5/6 specialists), the total agent-fleet cost is **~$1.08/hr**
across nine AgentCore Runtimes — the orchestrator alone routes to all
of them, so leaving them deployed is the price of having the
"data-engineering swarm" online.

