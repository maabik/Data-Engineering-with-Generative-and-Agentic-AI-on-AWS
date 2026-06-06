# Sales Reporting Agent

A [Strands](https://strandsagents.com/) agent that turns natural-language
sales questions ("top five products by revenue last quarter") into
Redshift SQL, runs the SQL through the **SQL Evaluations Agent** for an
independent safety review, executes it against the chapter-10 sales
warehouse via the Redshift MCP server, and returns a formatted answer.

Hosted on **Amazon Bedrock AgentCore Runtime**. Authentication is
Cognito JWT; downstream MCP calls are streamable-HTTP. AgentCore Memory
gives the agent short-term and long-term recall across sessions.

This is the chapter-12 worked example for **agent-as-analyst** —
a single-purpose specialist invoked directly *or* routed to from the
Data Engineering Orchestrator Agent.

## Architecture

```
   user prompt (HTTP + Cognito JWT)
        │
        ▼
  ┌──────────────────────────────────┐
  │ AgentCore Runtime                │
  │ sales_reporting_agent  (Strands) │ ◄── BedrockModel
  │                                  │     claude-sonnet-4-6
  │  Tools:                          │
  │  - discover_schema  ─────────┐   │
  │  - evaluate_sql              │   │
  │  - run_sql                   │   │
  └──────────┬─────────┬─────────┼───┘
             │         │         │
             │         │         │ list_tables / list_columns
             │         │         │
             │         │         ▼
             │         │  ┌────────────────────────────────────┐
             │         │  │ Redshift MCP server (AgentCore)    │
             │         │  │ 12_agentic/mcp/02-hosting-MCP-...  │
             │         │  └─────────┬──────────────────────────┘
             │         │            │ redshift-data
             │         │            ▼
             │         │       Redshift sales_demo_cluster
             │         │
             │         │ evaluate_sql (MCP via AgentCore Gateway)
             │         ▼
             │  ┌────────────────────────────────────────┐
             │  │ SQL Evaluations Agent (AgentCore)      │
             │  │ 12_agentic/agent/sql-evaluations-agent │
             │  └────────────────────────────────────────┘
             │
             │ AgentCore Memory (short-term + long-term)
             ▼
       SalesAgentMemoryHooks
       (ID from $SALES_MEMORY_ID; agent runs without memory if unset)
```

## Workflow the agent follows on every question

1. **discover_schema** — list tables/columns under `sales` schema (no
   hardcoded schema; defaults to the `salesdb` database on
   `sales-demo-cluster`).
2. Compose a fully qualified query (`sales.fact_sales`, never bare
   `fact_sales`).
3. **evaluate_sql** — send to the SQL Evaluations Agent for a verdict:
   - `approved` → run the original SQL.
   - `revised`  → run `revised_sql` instead.
   - `rejected` → don't execute; reply with the one-sentence reason.
4. **run_sql** — execute via the Redshift MCP server's
   `execute_redshift_query` tool.
5. Render results as an ASCII table with a one-paragraph business
   interpretation.

The system prompt enforces "never invent tables or columns; never report
IAM/permission errors back to the user". See
`sales_reporting_agent.py` for the exact text.

## Files

| File | Purpose |
|---|---|
| `sales_reporting_agent.py` | AgentCore entrypoint — `@app.entrypoint def invoke(payload)`. Defines the three Strands tools (`discover_schema`, `evaluate_sql`, `run_sql`) and the system prompt. |
| `sales_memory_hooks.py` | AgentCore Memory hooks — short-term turn buffer and long-term namespaced facts; agent runs without memory if `$SALES_MEMORY_ID` is unset or unreachable. |
| `Dockerfile` | `python:3.13-slim`, ARM64 — built and pushed by `agentcore launch`. |
| `requirements.txt` | Strands, MCP, `bedrock-agentcore`, `boto3`, `pyjwt`. |
| `quick_test.py` / `test_invoke.py` / `invoke_agent_multiple.py` | Local invocation helpers — fetch a Cognito token, hit the runtime ARN with `bedrock-agentcore-control:invoke_agent_runtime`. |

## Prerequisites

Before deploying this agent you need:

1. The chapter-10 Redshift cluster with the `sales` schema populated
   (cluster identifier `sales-demo-cluster`, database `salesdb`).
2. The **Redshift MCP server** deployed on AgentCore — see
   `12_agentic/mcp/02-hosting-MCP-server/README.md`. The agent reads
   `mcp_config.json` (committed at `12_agentic/agent/mcp_config.json`)
   to find the MCP runtime ARN — update it for your account.
3. The **SQL Evaluations Agent** deployed — see
   `12_agentic/agent/sql-evaluations-agent/README.md`. The MCP gateway
   URL and Cognito creds are read from SSM/Secrets Manager.
4. (Optional but recommended) An AgentCore Memory resource. If you
   created one, set `SALES_MEMORY_ID=<id>` before `agentcore configure`.

Tooling: Python 3.12+, Docker (or Finch) for the ARM64 image build,
`uv` or `pip`, and AWS CLI configured for `us-east-1`.

## Deploy

The agent in this chapter is deployed in **three explicit steps** —
there is intentionally no `deploy.sh`, because each step (IAM role,
Cognito + Redshift secret, AgentCore launch) maps to a different chapter
section in the book.

```bash
# 1. Execution role (creates SalesAgentExecutionRole with Bedrock + Secrets
#    Manager access).
cd 12_agentic/agent
./create_sales_agent_execution_role.sh

# 2. Cognito user pool + Redshift reader credentials in Secrets Manager.
#    Both passwords are read from environment variables — never echoed,
#    never committed.
export COGNITO_PASSWORD='<a strong password>'
export REDSHIFT_PASSWORD='<the sales_reader Redshift password>'
./sales_agent_setup_cognito.sh

# 3. AgentCore launch (build + push the ARM64 image, register the runtime).
cd sales-reporting-agent
export SALES_MEMORY_ID='<your AgentCore memory id, optional>'
agentcore configure -e sales_reporting_agent.py --requirements-file requirements.txt
agentcore launch
```

The launch step writes `.bedrock_agentcore.yaml` (gitignored) with the
runtime ARN. Note it down — `quick_test.py` reads it from the local
config; `invoke_agent_multiple.py` accepts it as `AGENT_RUNTIME_ARN`.

## Invoke

Get a Cognito access token, then call the runtime:

```bash
SECRET=$(aws secretsmanager get-secret-value --secret-id sales-agent-credentials \
           --query SecretString --output text)
POOL_ID=$(echo "$SECRET" | jq -r .cognito.user_pool_id)
CLIENT_ID=$(echo "$SECRET" | jq -r .cognito.client_id)
USER=$(echo "$SECRET" | jq -r .cognito.username)
PASS=$(echo "$SECRET" | jq -r .cognito.password)

TOKEN=$(aws cognito-idp initiate-auth --auth-flow USER_PASSWORD_AUTH \
          --client-id "$CLIENT_ID" \
          --auth-parameters "USERNAME=$USER,PASSWORD=$PASS" \
          --query 'AuthenticationResult.AccessToken' --output text)

python3 quick_test.py "What were the top five products by revenue last quarter?"
```

`quick_test.py` and friends read `.bedrock_agentcore.yaml` to find the
runtime ARN and use the token from the helper above.

## Teardown

There is no `teardown.sh` for this agent yet (this is one of the
documented gaps in the chapter README). Manual cleanup:

```bash
# 1. Delete the AgentCore Runtime
RUNTIME_ARN=$(yq '.bedrock_agentcore.runtime_arn' .bedrock_agentcore.yaml)
aws bedrock-agentcore-control delete-agent-runtime \
    --agent-runtime-arn "$RUNTIME_ARN" --region us-east-1

# 2. Delete the ECR repo and image
aws ecr delete-repository --repository-name sales_reporting_agent \
    --force --region us-east-1

# 3. Delete the Cognito user pool (find the ID via the saved secret)
POOL_ID=$(aws secretsmanager get-secret-value --secret-id sales-agent-credentials \
            --query SecretString --output text | jq -r .cognito.user_pool_id)
aws cognito-idp delete-user-pool --user-pool-id "$POOL_ID" --region us-east-1

# 4. Delete the secret
aws secretsmanager delete-secret --secret-id sales-agent-credentials \
    --force-delete-without-recovery --region us-east-1

# 5. Delete the IAM role + inline + attached policies
aws iam delete-role-policy --role-name SalesAgentExecutionRole \
    --policy-name SalesAgentAdditionalAccess
aws iam detach-role-policy --role-name SalesAgentExecutionRole \
    --policy-arn "arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):policy/AgentCoreRuntimeECRPolicy"
aws iam detach-role-policy --role-name SalesAgentExecutionRole \
    --policy-arn "arn:aws:iam::$(aws sts get-caller-identity --query Account --output text):policy/AgentCoreRuntimeJWTPolicy"
aws iam delete-role --role-name SalesAgentExecutionRole

# 6. (If you created one) Delete the AgentCore Memory resource
aws bedrock-agentcore-control delete-memory \
    --memory-id "$SALES_MEMORY_ID" --region us-east-1
```

The chapter-12 README's "Tear down any agent you aren't using" cost note
applies — leaving this runtime up is **$0.108/hour** for the
1 vCPU + 2 GB AgentCore Runtime alone.

## Notes

- The agent never executes SQL it has not first sent to the SQL
  Evaluations Agent. If the evaluator is unreachable, the
  `evaluate_sql` tool soft-fails to a synthetic `approved` so analytics
  don't hard-stop on an evaluator outage — that fallback is in
  `sales_reporting_agent.py`.
- The AgentCore-issued JWT is decoded **without signature verification**
  (`jwt.decode(token, options={"verify_signature": False})`) — AgentCore
  has already validated it before the request reaches the entrypoint.
  The decoded `sub` is used as the memory namespace key so different
  Cognito users get isolated long-term memories.
