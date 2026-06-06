# SQL Evaluations Agent

A separation-of-concerns SQL reviewer. Another agent generates SQL, this agent
inspects it for **errors**, **optimizations**, and **security risks**, and
returns a structured verdict — optionally with a rewritten `revised_sql`.

The agent runs on **Amazon Bedrock AgentCore Runtime** and is also exposed as
an **MCP tool** through **AgentCore Gateway** so any MCP-aware caller can use
it like a tool. Each invocation reads a steering doc on S3 and may append a
short lesson, so the agent's heuristics improve over time.

## Architecture

```
                 ┌─────────────────────────────────┐
   any MCP       │  AgentCore Gateway              │   CUSTOM_JWT (Cognito)
   client  ───►  │  sql-evaluations-agent-gw       │ ◄─── access token
                 │  tool: evaluations___           │
                 │        evaluate_sql             │
                 └────────────┬────────────────────┘
                              │ Lambda target
                              ▼
                 ┌─────────────────────────────────┐
                 │  Lambda                         │
                 │  sql-evaluations-agent-tools    │
                 └────────────┬────────────────────┘
                              │ invoke_agent_runtime
                              ▼
                 ┌─────────────────────────────────┐
                 │  AgentCore Runtime              │
                 │  sql_evaluations_agent          │ ◄─── BedrockModel
                 │  (Strands Agent)                │      claude-sonnet-4-6
                 └────────────┬────────────────────┘
                              │ get/put_object
                              ▼
                 ┌─────────────────────────────────┐
                 │  S3 steering-doc.md             │
                 │  (versioned, 64KB cap)          │
                 └─────────────────────────────────┘
```

Direct callers (e.g. a sibling agent) can also bypass the Gateway and invoke
the Runtime ARN with `bedrock-agentcore.invoke_agent_runtime`.

## What it returns

The agent always returns a single JSON object:

```json
{
  "status": "approved | revised | rejected",
  "findings": {
    "errors":        [{"severity": "low|medium|high", "issue": "...", "fix": "..."}],
    "optimizations": [{"severity": "low|medium|high", "issue": "...", "fix": "..."}],
    "security":      [{"severity": "low|medium|high", "issue": "...", "fix": "..."}]
  },
  "revised_sql": "string or null",
  "reasoning": "1-3 sentence summary"
}
```

- `approved` — SQL is fine; `revised_sql` is `null`.
- `revised`  — agent rewrote it; `revised_sql` holds the corrected SQL.
- `rejected` — unsafe/unfixable; reasoning explains why.

## Steering doc (running memory)

Path: `s3://${PREFIX}-${ACCOUNT}-${REGION}/steering-doc.md` (versioned).

- The agent reads the doc at the start of every evaluation.
- After the review, it appends one short timestamped lesson if the
  evaluation surfaced something non-obvious (skipped otherwise).
- The doc is bounded at **64 KB**: when it would grow beyond that, the
  header is preserved and only the most recent ~500 lessons are kept.
- Seed it (or reset it) any time with `scripts/seed_steering_doc.py`.

## Deploy

Prerequisites: AWS credentials (default profile), region `us-east-1`, and
either `docker buildx` or `finch` for the ARM64 image build.

```bash
bash deploy.sh
```

Single-shot, idempotent. Provisions:

| Resource           | Name                              |
|--------------------|-----------------------------------|
| S3 bucket          | `sql-evaluations-agent-${ACCOUNT}-${REGION}` |
| IAM roles          | `sql-evaluations-agent-runtime-role`, `-lambda-role`, `-gateway-role` |
| Cognito user pool  | `sql-evaluations-agent-pool` + `-resource` + `-client` + `-user` |
| Secret             | `sql-evaluations-agent/gateway-credentials` |
| Lambda             | `sql-evaluations-agent-tools`     |
| Gateway            | `sql-evaluations-agent-gw` (MCP, CUSTOM_JWT) |
| Gateway target     | `evaluations` (Lambda, `evaluate_sql` tool) |
| ECR repo           | `sql-evaluations-agent` (linux/arm64) |
| AgentCore Runtime  | `sql_evaluations_agent`           |
| SSM parameters     | `/sql-evaluations-agent/agent_runtime_arn`, `/sql-evaluations-agent/gateway_url` |

## Invoke

### Direct (boto3 → Runtime)

```bash
AGENT_RUNTIME_ARN=$(aws ssm get-parameter --name /sql-evaluations-agent/agent_runtime_arn \
  --query Parameter.Value --output text)

AGENT_RUNTIME_ARN="$AGENT_RUNTIME_ARN" \
  python3 scripts/invoke_runtime.py "SELECT * FROM users WHERE id = 1"
```

### Via MCP Gateway

```bash
GATEWAY_URL=$(aws ssm get-parameter --name /sql-evaluations-agent/gateway_url \
  --query Parameter.Value --output text)

GATEWAY_URL="$GATEWAY_URL" SECRET_NAME=sql-evaluations-agent/gateway-credentials \
  python3 scripts/invoke_via_gateway.py "DELETE FROM orders"
```

The MCP client fetches a Cognito access token from
`sql-evaluations-agent/gateway-credentials`, opens an MCP session over streamable HTTP,
lists the advertised tools, and calls the evaluator.

> **Tool name gotcha.** AgentCore Gateway namespaces target tools as
> `<targetName>___<toolName>`. The MCP tool advertised on the wire is
> **`evaluations___evaluate_sql`**, not `evaluate_sql`. Either hard-code
> that name or discover it dynamically from `session.list_tools()`:
> ```python
> tools = await session.list_tools()
> tool_name = next(t.name for t in tools.tools if t.name.endswith("evaluate_sql"))
> ```

### Tool input shape

```json
{
  "sql":     "SELECT ... FROM ...",
  "context": "optional intent/schema notes",
  "dialect": "ansi | redshift | postgres | snowflake | athena | bigquery"
}
```

## Calling from another agent (worked example)

The sister `sales-reporting-agent` calls this evaluator before every
`run_sql`. The integration is three changes:

1. **Add an `evaluate_sql` tool** that opens a streamable-HTTP MCP session
   to the Gateway, gets a Cognito token from
   `sql-evaluations-agent/gateway-credentials`, calls `evaluations___evaluate_sql`, and
   returns the JSON verdict. Soft-fail to a synthetic `approved` if the
   evaluator is unreachable so analytics don't hard-stop on an evaluator
   outage.

2. **Update the system prompt** so the agent calls `evaluate_sql` *before*
   `run_sql` and honors the verdict:
   - `approved` → run the original SQL.
   - `revised`  → run `revised_sql` instead.
   - `rejected` → don't execute; tell the user one-sentence why.

3. **Grant the caller's runtime IAM role** access to the Gateway plumbing:
   ```json
   {
     "Effect": "Allow",
     "Action": "ssm:GetParameter",
     "Resource": "arn:aws:ssm:us-east-1:<acct>:parameter/sql-evaluations-agent/gateway_url"
   },
   {
     "Effect": "Allow",
     "Action": "secretsmanager:GetSecretValue",
     "Resource": "arn:aws:secretsmanager:us-east-1:<acct>:secret:sql-evaluations-agent/gateway-credentials-*"
   },
   {
     "Effect": "Allow",
     "Action": "cognito-idp:InitiateAuth",
     "Resource": "arn:aws:cognito-idp:us-east-1:<acct>:userpool/*"
   }
   ```

See `agent/sales-reporting-agent/sales_reporting_agent.py` for the full
integration (search for `evaluate_sql`).

## Project layout

```
sql-evaluations-agent/
├── agent/
│   ├── sql_evaluations_agent.py   # Strands agent + steering-doc tools
│   ├── requirements.txt
│   └── Dockerfile                 # python:3.13-slim, ARM64
├── tools/
│   └── lambda_function.py         # Gateway target → invoke_agent_runtime
├── scripts/
│   ├── invoke_runtime.py          # direct boto3 invoke
│   ├── invoke_via_gateway.py      # MCP client invoke
│   └── seed_steering_doc.py       # idempotent steering-doc seed
├── deploy.sh                      # one-shot deploy
└── teardown.sh                    # one-shot teardown
```

## Teardown

```bash
bash teardown.sh
```

Deletes (in safe order): runtime → gateway target/gateway → Lambda → ECR
repo → secret → Cognito user pool → IAM roles → SSM params → S3 bucket
(versioned objects + delete markers wiped first).
