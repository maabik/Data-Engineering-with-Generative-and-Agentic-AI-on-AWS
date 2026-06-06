#!/usr/bin/env python3
"""Reference Strands agent for the knowledge-graph MCP server.

Mirrors ``12_agentic/agent/sales-reporting-agent/sales_reporting_agent.py``:
mints a fresh Cognito bearer on each invocation, opens an MCP session,
and exposes the four KG tools to the model.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import timedelta
from pathlib import Path

import boto3
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent, tool
from strands.models import BedrockModel

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("KG_AGENT_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
AGENT_ARN_PARAM = os.environ.get("KG_MCP_AGENT_ARN_PARAM", "/kg/mcp/agent_arn")
CREDENTIALS_SECRET = os.environ.get("KG_MCP_CREDENTIALS_SECRET", "/kg/mcp/credentials")

SESSION_ID = str(uuid.uuid4())
log = logging.getLogger(__name__)


def _load_system_prompt() -> str:
    """Locate ``system_prompt.md`` whether the file lives next to this
    module (source layout) or inside the knowledge_graph_agent package (deploy
    layout where ``agent.py`` is staged at ``/app/agent.py``).
    """
    here = Path(__file__).parent
    candidates = [
        here / "prompts" / "system_prompt.md",
        here / "knowledge_graph_agent" / "agent" / "prompts" / "system_prompt.md",
    ]
    for path in candidates:
        if path.exists():
            return path.read_text()
    raise FileNotFoundError(
        "Could not locate system_prompt.md; tried: "
        + ", ".join(str(p) for p in candidates)
    )


SYSTEM_PROMPT = _load_system_prompt()


def _mcp_url_and_token() -> tuple[str, str]:
    ssm = boto3.client("ssm", region_name=REGION)
    secrets = boto3.client("secretsmanager", region_name=REGION)

    agent_arn = ssm.get_parameter(Name=AGENT_ARN_PARAM)["Parameter"]["Value"]
    creds = json.loads(secrets.get_secret_value(SecretId=CREDENTIALS_SECRET)["SecretString"])

    cognito = boto3.client("cognito-idp", region_name=REGION)
    auth = cognito.initiate_auth(
        ClientId=creds["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
    )
    bearer = auth["AuthenticationResult"]["AccessToken"]

    encoded_arn = agent_arn.replace(":", "%3A").replace("/", "%2F")
    url = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com/"
        f"runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
    )
    return url, bearer


async def _call_mcp(tool_name: str, arguments: dict) -> str:
    url, bearer = _mcp_url_and_token()
    headers = {
        "authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with streamablehttp_client(url, headers, timeout=timedelta(seconds=60)) as streams:
        read_stream, write_stream, _ = streams
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(name=tool_name, arguments=arguments)
            if result.isError:
                return f"Tool {tool_name!r} error: {result.content[0].text}"
            return result.content[0].text


@tool
async def kg_search(query: str, mode: str = "hybrid") -> str:
    """Search the knowledge graph (hybrid by default). See system prompt."""
    return await _call_mcp("kg_search", {"query": query, "mode": mode})


@tool
async def kg_get_entity(entity_id: str) -> str:
    """Fetch the full property bag of a single entity."""
    return await _call_mcp("kg_get_entity", {"entity_id": entity_id})


@tool
async def kg_find_path(source_id: str, target_id: str, max_depth: int = 4) -> str:
    """Shortest path between two entities (max_depth bounded at 6)."""
    return await _call_mcp(
        "kg_find_path",
        {"source_id": source_id, "target_id": target_id, "max_depth": max_depth},
    )


@tool
async def kg_summarize_subgraph(entity_id: str, depth: int = 2) -> str:
    """Compact summary of an entity's neighborhood."""
    return await _call_mcp("kg_summarize_subgraph", {"entity_id": entity_id, "depth": depth})


def create_knowledge_graph_agent() -> Agent:
    """Remote-mode agent — tools route through the deployed MCP server."""
    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    return Agent(
        model=model,
        tools=[kg_search, kg_get_entity, kg_find_path, kg_summarize_subgraph],
        system_prompt=SYSTEM_PROMPT,
    )


def create_local_knowledge_graph_agent() -> Agent:
    """Local-mode agent — calls the tool implementations in-process.

    Used by ``run_local()`` so a developer can iterate without redeploying
    the MCP server. Same prompts, same model, identical tool surface —
    only the transport changes.
    """
    from knowledge_graph_agent.mcp_server.tools import (
        kg_find_path as _local_find_path,
        kg_get_entity as _local_get_entity,
        kg_search as _local_search,
        kg_summarize_subgraph as _local_summarize,
    )

    @tool
    def kg_search_local(query: str, mode: str = "hybrid") -> str:
        """Search the knowledge graph (hybrid by default)."""
        return _local_search(query=query, mode=mode)

    @tool
    def kg_get_entity_local(entity_id: str) -> str:
        """Fetch the full property bag of a single entity."""
        return _local_get_entity(entity_id=entity_id)

    @tool
    def kg_find_path_local(source_id: str, target_id: str, max_depth: int = 4) -> str:
        """Shortest path between two entities."""
        return _local_find_path(source_id=source_id, target_id=target_id, max_depth=max_depth)

    @tool
    def kg_summarize_subgraph_local(entity_id: str, depth: int = 2) -> str:
        """Compact summary of an entity's neighborhood."""
        return _local_summarize(entity_id=entity_id, depth=depth)

    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    return Agent(
        model=model,
        tools=[
            kg_search_local,
            kg_get_entity_local,
            kg_find_path_local,
            kg_summarize_subgraph_local,
        ],
        system_prompt=SYSTEM_PROMPT,
    )


app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload):
    """AgentCore Runtime entrypoint."""
    user_input = payload.get("prompt", "")
    agent = create_knowledge_graph_agent()
    response = agent(user_input)
    return response.message["content"][0]["text"]


def run_local(prompt: str) -> str:
    """Convenience helper for laptop use — bypasses AgentCore Runtime
    AND the deployed MCP server. The agent calls the tool functions
    directly against Neptune Analytics + S3 Vectors.
    """
    agent = create_local_knowledge_graph_agent()
    response = agent(prompt)
    return response.message["content"][0]["text"]


if __name__ == "__main__":
    app.run()
