#!/usr/bin/env python3
"""CDK entrypoint for the knowledge-graph-agent project.

Provisions four stacks; deploy them in order:

    cdk deploy KGNeptune KGIngestion KGObservability
    # AgentCore Runtime is deployed via the toolkit, not CDK — see
    # ``src/knowledge_graph_agent/mcp_server/deploy.py``. KGMcp here only stores
    # parameter-store/secret entries the runtime relies on.
    cdk deploy KGMcp
"""

import aws_cdk as cdk

from stacks.neptune_stack import NeptuneStack
from stacks.ingestion_stack import IngestionStack
from stacks.mcp_stack import McpStack
from stacks.observability_stack import ObservabilityStack

app = cdk.App()

neptune = NeptuneStack(
    app,
    "KGNeptune",
    description="Neptune Analytics graph + IAM + KMS for the GraphRAG agent",
)

ingestion = IngestionStack(
    app,
    "KGIngestion",
    graph_arn=neptune.graph_arn,
    documents_bucket_name=neptune.documents_bucket.bucket_name,
    description="Step Functions + Lambda extraction pipeline",
)
ingestion.add_dependency(neptune)

mcp = McpStack(
    app,
    "KGMcp",
    graph_arn=neptune.graph_arn,
    description="Parameter Store + Secrets Manager entries for the KG MCP server",
)
mcp.add_dependency(neptune)

ObservabilityStack(
    app,
    "KGObservability",
    graph_id_param=neptune.graph_id_param_name,
    description="CloudWatch dashboards for the GraphRAG agent",
)

app.synth()
