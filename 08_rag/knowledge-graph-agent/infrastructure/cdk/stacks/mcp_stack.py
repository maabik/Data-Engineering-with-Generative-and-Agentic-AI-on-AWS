"""KGMcp — Parameter Store + Secrets Manager entries the MCP server depends on.

The MCP server itself is deployed via the bedrock-agentcore-starter-toolkit
(see ``src/knowledge_graph_agent/mcp_server/deploy.py``). This stack just creates the
SSM parameter and Secrets Manager secret slots so deploy.py has a target.
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    SecretValue,
    Stack,
    aws_secretsmanager as secretsmanager,
    aws_ssm as ssm,
)
from constructs import Construct


class McpStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        graph_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Placeholder; deploy.py overwrites with the real ARN.
        ssm.StringParameter(
            self,
            "AgentArnParam",
            parameter_name="/kg/mcp/agent_arn",
            string_value="not-yet-deployed",
            description="ARN of the deployed AgentCore Runtime for the KG MCP server",
        )

        # Cognito credentials are written by the agentcore deploy step.
        secretsmanager.Secret(
            self,
            "McpCredentials",
            secret_name="/kg/mcp/credentials",
            description="Cognito client_id / username / password used by MCP clients",
            secret_string_value=SecretValue.unsafe_plain_text(
                '{"placeholder": "populated by mcp_server/deploy.py"}'
            ),
        )

        cdk.CfnOutput(self, "McpAgentArnParam", value="/kg/mcp/agent_arn")
        cdk.CfnOutput(self, "McpCredentialsSecret", value="/kg/mcp/credentials")
