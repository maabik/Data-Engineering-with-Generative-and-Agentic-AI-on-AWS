"""Integration tests for the deployed MCP server.

Skipped automatically unless ``KG_INTEGRATION=1`` is set in the
environment, because they depend on a live AgentCore Runtime.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("KG_INTEGRATION") != "1",
    reason="KG_INTEGRATION=1 not set",
)


def test_mcp_list_tools():
    """Confirm the deployed runtime advertises the four expected tools."""
    import asyncio
    import json
    from datetime import timedelta

    import boto3
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    region = os.environ.get("AWS_REGION", "us-east-1")
    ssm = boto3.client("ssm", region_name=region)
    secrets = boto3.client("secretsmanager", region_name=region)
    cognito = boto3.client("cognito-idp", region_name=region)

    agent_arn = ssm.get_parameter(Name="/kg/mcp/agent_arn")["Parameter"]["Value"]
    creds = json.loads(secrets.get_secret_value(SecretId="/kg/mcp/credentials")["SecretString"])
    auth = cognito.initiate_auth(
        ClientId=creds["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
    )
    bearer = auth["AuthenticationResult"]["AccessToken"]
    encoded = agent_arn.replace(":", "%3A").replace("/", "%2F")
    url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded}/invocations?qualifier=DEFAULT"

    async def _check():
        headers = {
            "authorization": f"Bearer {bearer}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        async with streamablehttp_client(url, headers, timeout=timedelta(seconds=30)) as (r, w, _):
            async with ClientSession(r, w) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert {"kg_search", "kg_get_entity", "kg_find_path", "kg_summarize_subgraph"}.issubset(names)

    asyncio.run(_check())
