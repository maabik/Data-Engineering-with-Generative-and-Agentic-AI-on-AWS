# Infrastructure (CDK, Python)

Four stacks; deploy in order:

```bash
cd infrastructure/cdk
pip install -r ../../requirements.txt aws-cdk-lib constructs
cdk synth
cdk deploy KGNeptune KGIngestion KGObservability KGMcp
```

| Stack             | What it provisions                                                                |
| ----------------- | --------------------------------------------------------------------------------- |
| `KGNeptune`       | Neptune Analytics graph (with vector search), KMS key, S3 buckets (documents + vectors), shared IAM role, SSM parameters |
| `KGIngestion`     | Lambda + Step Functions extraction pipeline                                       |
| `KGMcp`           | SSM parameter and Secrets Manager secret slots that `mcp_server/deploy.py` populates |
| `KGObservability` | CloudWatch dashboard for Bedrock + Neptune metrics                                |

## After deploy

The Neptune Analytics graph ID lands in `/kg/graph_id`. Set it once for the
local Python package:

```bash
export KG_GRAPH_ID=$(aws ssm get-parameter --name /kg/graph_id --query 'Parameter.Value' --output text)
```

The MCP server is **not** deployed by CDK — Bedrock AgentCore Runtime is
deployed via the `bedrock-agentcore-starter-toolkit`:

```bash
python -m knowledge_graph_agent.mcp_server.deploy
```

This pattern matches `12_agentic/mcp/02-hosting-MCP-server/`.

## Teardown

```bash
cdk destroy KGMcp KGObservability KGIngestion KGNeptune
```

The Neptune graph and S3 buckets retain by default — the CDK code
explicitly sets `RemovalPolicy.RETAIN` on Neptune and the buckets to
prevent accidental data loss. Empty and remove them manually if you
truly want a clean teardown.
