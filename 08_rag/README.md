# 08 — Retrieval-augmented generation

> Code companion for **[Data Engineering with Generative and Agentic AI on AWS](../README.md)** — *Building an AI-Augmented Data Practice for the Enterprise* by Justin J. Leto (Apress, 2026). Book site: <https://agenticdataengineering.ai>. Apress GitHub: <https://github.com/Apress/Data-Engineering-with-Generative-and-Agentic-AI-on-AWS>.

A small RAG pipeline on Amazon S3 Vectors and Bedrock Titan Text
Embeddings v2, plus a production GraphRAG flagship example on Amazon
Neptune Analytics with a property graph, hybrid graph + vector
retrieval, and an MCP server on AgentCore Runtime.

## Default region

Both projects default to **us-east-1**. `s3_vectors` honors `AWS_REGION`
on the CLI; `knowledge-graph-agent` deploys via CDK and reads the
region from your active AWS profile (or from the `--region` flag at
deploy time).

## Projects

| Project | What it deploys |
|---|---|
| [`s3_vectors/`](s3_vectors/) | Library + CLI for S3 Vectors: ingest documents, query by similarity, filter by metadata, tear down. Pytest suite included. |
| [`knowledge-graph-agent/`](knowledge-graph-agent/) | Production GraphRAG: Bedrock entity/relationship extraction (Claude 3.7/4.5 Sonnet) + Nova Multimodal Embeddings, property graph on Neptune Analytics, MCP server on AgentCore Runtime, reference Strands agent. |

## Cost estimate (us-east-1, light active workload)

| Project | Service | Hourly fixed | Hourly active | Notes |
|---|---|---:|---:|---|
| s3_vectors | S3 Vectors index bucket | $0.0000 | $0.0001 | $0.06 per 1M vectors-month + $0.001 per 1K queries. Negligible at demo scale. |
| s3_vectors | Bedrock Titan v2 embeddings | $0.0000 | $0.0001 | $0.00002 per 1K input tokens — cheap. |
| knowledge-graph-agent | Neptune Analytics 128 m-NCU | $0.1972 | $0.1972 | **Always-on capacity ≈ $144/month**. Bills 24/7 unless you delete the graph. |
| knowledge-graph-agent | AgentCore Runtime (1 vCPU + 2 GB) | $0.1080 | $0.1080 | $0.0895/vCPU-hr + $0.00945/GB-hr while serving. |
| knowledge-graph-agent | KMS CMK | $0.0014 | $0.0014 | $1/key/month. |
| knowledge-graph-agent | S3 buckets × 2 + Cognito + Step Functions + Lambda + SSM + Secrets Manager + CloudWatch | $0.0006 | $0.0050 | Cognito free at demo MAU; everything else pay-per-use. |
| knowledge-graph-agent | Bedrock per-query (extraction + reasoning) | $0.0000 | $0.0200 | Sonnet ~$0.018 per agent question; Nova embeddings ~$0.0001 per query. |
| **Chapter total** | | **~$0.3072** | **~$0.3318** | Neptune Analytics is the dominant cost — leave it deployed only when you're actively querying. |

The chapter's standing cost is dominated by **Neptune Analytics 128
m-NCU at ~$0.20/hr (~$144/month)** plus the AgentCore Runtime. The
sample workload measures the per-query cost at ~$0.019 — see
[`knowledge-graph-agent/docs/operating_costs.md`](knowledge-graph-agent/docs/operating_costs.md)
for measured numbers.

To cut the bill: delete the Neptune Analytics graph between sessions
(re-ingest is fast for the sample 8-document corpus); the AgentCore
Runtime container scales to zero when no requests are in flight.
