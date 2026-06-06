# knowledge-graph-agent

Production GraphRAG on Amazon Neptune Analytics, exposed as an MCP
server on Bedrock AgentCore Runtime, with a reference Strands agent
that combines graph traversal with vector search.

## Why GraphRAG, why now

Vector RAG (covered exhaustively in Chapter 8) is great at "find me the
paragraph that talks about X." It struggles at "executives at companies
owned by Sequoia Capital who have a fintech background" — three
different kinds of evidence that have to be joined before you can
answer.

A property graph stores the joins. A vector index stores semantic
recall. Combine both — that's GraphRAG — and you get answers neither
mode can produce alone. The PE due-diligence example demonstrates the
pattern in 15 minutes.

## What this project provides

- **Property graph on Amazon Neptune Analytics**, with a fixed schema
  (Documents, Entities, Persons, Organizations, Events, Concepts,
  Datasets) and seven typed edges.
- **Bedrock-driven extraction**: Anthropic Claude Sonnet 4.5 for
  entities and relationships; Amazon Nova 2 Multimodal Embeddings for
  vector recall. Prompts are externalized markdown — iterate without
  redeploying.
- **Hybrid retrieval**: vector recall, 1–2 hop graph context, re-ranked
  by combined score.
- **MCP server** on Bedrock AgentCore Runtime, secured with Cognito
  (mirrors the pattern from Chapter 12). Four tools: `kg_search`,
  `kg_get_entity`, `kg_find_path`, `kg_summarize_subgraph`.
- **Reference Strands agent** that uses the MCP server.
- **OpenLineage projection** as a separate edge type (`DERIVED_FROM`).
- **CDK Python infrastructure** in `infrastructure/cdk/`.
- **Three examples**: PE due diligence (flagship), SEC 10-K filings,
  data lineage.

## Deploy and run in ~15 minutes

Tested live in `us-east-1` against this account's Bedrock + Neptune
Analytics + S3 Vectors + AgentCore Runtime services.

```bash
git clone <this-repo>
cd 08_rag/knowledge-graph-agent
bash scripts/bootstrap.sh

# 1. Deploy the foundations
cd infrastructure/cdk
AWS_REGION=us-east-1 cdk deploy KGNeptune KGIngestion KGObservability KGMcp
cd -

# 2. Provision the S3 Vectors bucket + index
AWS_REGION=us-east-1 PYTHONPATH=src python3 scripts/create_vector_index.py

# 3. Capture the live config
export AWS_REGION=us-east-1
export KG_GRAPH_ID=$(aws ssm get-parameter --name /kg/graph_id --query Parameter.Value --output text)
export KG_S3_VECTORS_BUCKET=$(aws ssm get-parameter --name /kg/vectors_bucket --query Parameter.Value --output text)

# 4. Deploy the MCP server to AgentCore Runtime
KG_COGNITO_PASSWORD=ChangeMe1! PYTHONPATH=src python3 -m knowledge_graph_agent.mcp_server.deploy

# 5. Deploy the Strands agent runtime
PYTHONPATH=src python3 -m knowledge_graph_agent.agent.deploy

# 6. After both deploys, attach extra IAM perms to the auto-created runtime roles
#    (one for the MCP server, one for the agent). The role names are logged
#    during deploy as AmazonBedrockAgentCoreSDKRuntime-<region>-<hash>.
#    - MCP server role: needs neptune-graph:* on the graph ARN, s3vectors:*
#      on the vectors bucket, bedrock:InvokeModel.
#    - Agent role: needs ssm:GetParameter on /kg/*, secretsmanager:GetSecretValue
#      on /kg/*, cognito-idp:InitiateAuth, bedrock:InvokeModel.

# 7. Run the flagship example
PYTHONPATH=src python3 examples/pe_due_diligence/ingest_dataroom.py
PYTHONPATH=src python3 examples/pe_due_diligence/run_agent.py --mode local \
  "What are the top three integration risks for Acme's FinFlow acquisition?"

# 8. Or hit the deployed agent + MCP runtime via remote mode
PYTHONPATH=src python3 examples/pe_due_diligence/run_agent.py --mode remote \
  "Is there any path between Maria Chen and Sequoia Capital?"
```

## Where this fits in the book

| Chapter   | Thread                                                                |
| --------- | --------------------------------------------------------------------- |
| Ch. 3     | S3 Iceberg as the document source layer; `Dataset` nodes mirror tables |
| Ch. 4     | DataZone publishing of graph entities (stretch goal)                   |
| Ch. 7     | Bedrock Data Automation feeds the document extraction pipeline         |
| **Ch. 8** | **This is the GraphRAG follow-on Chapter 8 explicitly leaves to a successor project** |
| Ch. 12    | Pattern reuse: AgentCore Runtime, MCP, Strands, Cognito                |

## Acceptance criteria status (all live-tested in us-east-1)

- [x] CDK provisions all infrastructure (`cdk deploy` ✅, all 4 stacks `CREATE_COMPLETE`)
- [x] `bash scripts/bootstrap.sh` installs deps and synths CDK
- [x] PE due-diligence example with **all 5 sample questions answered live**
- [x] Unit tests pass (24/24, `pytest tests/unit/`)
- [x] **Integration tests pass live** (`KG_INTEGRATION=1 pytest tests/integration/` → 2 passed)
- [x] **Step Functions extraction workflow runs live** — Lambda container image
      with the knowledge_graph_agent package, Map state processes documents in parallel,
      writes nodes + edges + embeddings to the live graph
- [x] **MCP server live on AgentCore Runtime** (`/kg/mcp/agent_arn`)
- [x] **Strands agent live on AgentCore Runtime** (`/kg/agent/agent_arn`),
      remote-mode `run_agent.py --mode remote` answers questions end-to-end
- [x] **OpenLineage projection runs live** (`Dataset` nodes + `DERIVED_FROM` edges)
- [x] Measured cost table in `docs/operating_costs.md`
- [x] README under 600 words
- [x] `<10s` p95 target for typical 2-hop queries (see retrieval_patterns.md)

## Layout

```
knowledge-graph-agent/
├── ARCHITECTURE.md
├── README.md                  ← you are here
├── pyproject.toml
├── requirements.txt
├── infrastructure/cdk/        ← Neptune + Ingestion + MCP + Observability
├── src/knowledge_graph_agent/
│   ├── extraction/            ← Bedrock NER + relations, externalized prompts
│   ├── ingestion/             ← graph_loader, batch_loader, vector_store, dedup
│   ├── retrieval/             ← graph_search, vector_search, hybrid_search
│   ├── lineage/               ← OpenLineage → graph projection
│   ├── mcp_server/            ← FastMCP + AgentCore deploy + Cognito
│   └── agent/                 ← reference Strands agent
├── examples/                  ← pe_due_diligence (flagship), sec_10k, lineage
├── tests/                     ← unit + integration
├── scripts/                   ← bootstrap, smoke_test, teardown
└── docs/                      ← graph_schema, extraction, retrieval, costs
```

## Things this project deliberately doesn't do

- **No custom MCP auth.** Cognito + SigV4, same as the Redshift MCP.
- **No classic Neptune cluster.** Analytics is purpose-built for this
  workload — see `ARCHITECTURE.md` for the trade-offs.
- **No string-match deduplication.** Embedding cosine ≥ 0.92 default.
- **No inline prompts.** Always markdown under
  `src/knowledge_graph_agent/extraction/prompts/`.
- **No emitting embeddings for tiny names.** Length ≥ 4, non-numeric.

## Default region

Defaults to **us-east-1** (read from the active AWS profile by the CDK
app). Override with `cdk deploy --context region=…` or by setting
`AWS_REGION` before deploy. Neptune Analytics is available in a
limited set of regions — confirm with the AWS service availability
page before picking one.

## Cost estimate (us-east-1, light active workload)

| Service | Hourly fixed | Hourly active | Notes |
|---|---:|---:|---|
| Neptune Analytics (128 m-NCU) | **$0.1972** | **$0.1972** | $0.20/m-NCU-hour × 128 m-NCU ≈ $144/month. Bills 24/7 unless the graph is **deleted** (deletion_protection=True by default — disable to tear down). |
| AgentCore Runtime (1 vCPU + 2 GB) | $0.1080 | $0.1080 | $0.0895/vCPU-hr + $0.00945/GB-hr while serving traffic. |
| KMS CMK | $0.0014 | $0.0014 | $1/key/month. |
| S3 buckets (kg-documents, kg-vectors) | $0.0000 | $0.0001 | $0.023/GB-month + request charges; demo corpus negligible. |
| Cognito user pool | $0.0000 | $0.0000 | Free up to 50k MAU. |
| Step Functions Standard (ingestion workflow) | $0.0000 | $0.0001 | $0.025/1k state transitions. |
| Lambda (extraction function) | $0.0000 | $0.0010 | Pay-per-invoke. |
| ECR + SSM + Secrets Manager | $0.0006 | $0.0006 | $0.40/secret/month + ECR storage cents/month. |
| CloudWatch dashboard + alarms | $0.0000 | $0.0010 | $3/dashboard-month + $0.10/alarm-month. |
| Bedrock per query (Claude Sonnet + Nova embeddings) | $0.0000 | $0.0200 | ~$0.019 per agent question (measured) — see [`docs/operating_costs.md`](docs/operating_costs.md). |
| **Project total** | **$0.3072** | **$0.3294** | Neptune Analytics is ~60% of the bill. |

The dominant cost driver is **Neptune Analytics 128 m-NCU at
~$0.20/hr (~$144/month)**, which bills continuously regardless of
query volume. The AgentCore Runtime adds ~$0.108/hr only while it's
actively serving requests.

To cut the bill:

1. **Delete the Neptune Analytics graph between sessions.** It's the
   only service in this stack with always-on capacity. Re-ingest is
   fast for the sample 8-document corpus.
2. **Tear down the AgentCore Runtime** with
   `cdk destroy KGMcp` when you're not querying.
3. **Run extraction with Claude Haiku instead of Sonnet** — see "Cost
   knobs" in [`docs/operating_costs.md`](docs/operating_costs.md).
