"""SigV4 → MCP bridge for Strands agents.

Wraps `mcp_proxy_for_aws`'s SigV4-signed HTTP transport in a Strands
`MCPClient` so an agent can authenticate to an AWS-hosted MCP server using
whatever IAM credentials its runtime already has.
"""
from __future__ import annotations

from typing import Any

from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client
from strands.tools.mcp import MCPClient


def strands_mcp_client(
    url: str,
    service: str = "lambda",
    region: str | None = None,
    profile: str | None = None,
    credentials: Any | None = None,
) -> MCPClient:
    """Return a Strands `MCPClient` that signs every request with SigV4.

    Parameters
    ----------
    url:
        Full URL of the MCP endpoint (e.g. the Lambda Function URL's `/mcp`
        path, or the API Gateway invoke URL).
    service:
        AWS service name for SigV4 key derivation. `lambda` for Function URLs,
        `execute-api` for API Gateway, `bedrock-agentcore` for AgentCore
        Runtime.
    region:
        AWS region the target lives in. If omitted, uses the region from the
        boto3 default session.
    profile:
        Optional AWS profile name. Falls back to the default credential chain.
    credentials:
        Optional `botocore.credentials.Credentials` for supplying pre-resolved
        credentials (e.g. from an assumed cross-account role).
    """

    def _factory():
        kwargs: dict[str, Any] = {"endpoint": url, "aws_service": service}
        if region is not None:
            kwargs["aws_region"] = region
        if profile is not None:
            kwargs["aws_profile"] = profile
        if credentials is not None:
            kwargs["credentials"] = credentials
        return aws_iam_streamablehttp_client(**kwargs)

    return MCPClient(_factory)
