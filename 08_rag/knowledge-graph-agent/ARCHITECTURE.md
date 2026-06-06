# Architecture

## High-level diagram

```
                           ┌──────────────────────┐
                           │    Bedrock — Claude  │
                           │   3.7 Sonnet (NER)   │
                           └──────────┬───────────┘
                                      │ Converse API
┌────────────┐    Step Funcs   ┌──────▼──────┐    Cypher    ┌────────────────────┐
│ S3         ├─────────────────► Lambda      ├──────────────► Neptune Analytics  │
│ documents  │   (per-doc Map) │ extract     │    MERGE     │ (property graph)   │
└────────────┘                 │ + embed     │              └─────────┬──────────┘
                               │ + load      │                        │
                               └──────┬──────┘                        │
                                      │ embed                         │ openCypher
                                      ▼                               │
                               ┌─────────────┐                        │
                               │ S3 Vectors  │◄── vector recall ──────┘
                               │ (Nova 1024) │
                               └──────┬──────┘
                                      │
                                      │
                               ┌──────▼──────┐
                               │ HybridSearch│ — re-rank vec + graph proximity
                               └──────┬──────┘
                                      │
                               ┌──────▼──────┐
                               │ FastMCP     │ ─ kg_search, kg_get_entity,
                               │ on AgentCore│   kg_find_path, kg_summarize_subgraph
                               │ Runtime     │
                               └──────┬──────┘
                                      │ MCP / SigV4 + Cognito
                               ┌──────▼──────┐
                               │ Strands     │
                               │ Agent       │
                               └─────────────┘
```

## Why Neptune Analytics, not classic Neptune

| Dimension      | Neptune Analytics                              | Neptune (cluster)                                    |
| -------------- | ---------------------------------------------- | ---------------------------------------------------- |
| Pricing        | Provisioned m-NCU, hourly, scales to zero      | Always-on instance(s), per-hour                      |
| Vector search  | First-class; graph + vector in one query plan  | Bring-your-own (OpenSearch sidecar)                  |
| Concurrency    | Lower than cluster — designed for analytics    | High concurrency, OLTP-style                         |
| Algorithms     | Built-in: PageRank, Node2Vec, BFS, etc.        | Limited                                              |
| Best for       | AI workloads with vector + traversal queries   | Transactional graph apps with high write fan-in      |

This project is squarely in the "AI workload" column. Picking Analytics
over the cluster is not a preference — it's a workload fit.

## Data plane

`knowledge_graph_agent.ingestion.graph_loader.GraphLoader` wraps the
`neptune-graph` boto3 data-plane client. All writes are MERGE-based, so
re-running ingest is idempotent. The loader bounds traversal depth at
the call site (max 4 for `find_neighbors`, max 6 for `find_path`) — the
agent cannot blow up the cluster with a runaway Cypher query.

## Auth flow (MCP)

Mirrors the Redshift MCP pattern from Chapter 12:

1. `setup_cognito_user_pool` provisions an idempotent user pool, client,
   and a permanent password for the service user.
2. AgentCore Runtime is configured with `customJWTAuthorizer` pointing
   at the Cognito discovery URL.
3. Clients (the Strands agent, integration tests, the `run_agent.py`
   helper) mint a fresh bearer token from Cognito on each invocation.
4. Bearer tokens are short-lived; we never persist them. Cognito
   credentials live in Secrets Manager at `/kg/mcp/credentials`.

SigV4 fallback isn't wired in by default — the Cognito path is the
primary route. If you need SigV4 (long-lived headless callers), copy
the pattern from `12_agentic/mcp/` and adjust the IAM policy on the
agent role.

## Extraction pipeline (Step Functions)

```
[S3 ListObjects] -> Map (concurrency=8):
    [Lambda: extract entities + relationships]
    -> [Lambda: embed]
    -> [Lambda: load to graph + vector store]
```

The Lambda code itself is a thin shim; the heavy lifting is in
`knowledge_graph_agent.ingestion.batch_loader.process_document`, which runs the
same way from a laptop. Lambda concurrency is bounded at 8 to avoid
hammering Bedrock — raise it once you've measured your account's
Bedrock RPS quota.

## Cost model

See `docs/operating_costs.md` for measured numbers. The biggest line
item is Neptune Analytics provisioned capacity (~$144/month at 128
m-NCU). For development, tear it down between sessions; for
production, the steady-state per-query cost dominates only after
~7,500 questions per month.

## Observability

`KGObservability` provisions a CloudWatch dashboard with three widgets:

- Bedrock token usage (input + output)
- Bedrock invocations
- Neptune Analytics latency + error rate

For per-query tracing, AgentCore Observability captures the agent's
tool calls and reasoning automatically.
