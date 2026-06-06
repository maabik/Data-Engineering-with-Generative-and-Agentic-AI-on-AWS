"""Strands agent that talks to the Lambda MCP target via SigV4.

Usage:
    python agent/agent.py "What's my caller identity?"

Requires Bedrock model access in the deploy region. For a credential-only
smoke test that skips the LLM, run `python agent/smoke_test.py` instead.
"""
from __future__ import annotations

import os
import sys

from strands import Agent

from sigv4_mcp_bridge import strands_mcp_client

MCP_URL = os.environ["MCP_FUNCTION_URL"].rstrip("/") + "/mcp"
REGION = os.environ.get("AWS_REGION") or os.environ["MCP_REGION"]
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")


def main() -> None:
    prompt = " ".join(sys.argv[1:]) or "List my caller identity."

    mcp = strands_mcp_client(url=MCP_URL, service="lambda", region=REGION)
    with mcp:
        agent = Agent(model=MODEL_ID, tools=mcp.list_tools_sync())
        print(agent(prompt))


if __name__ == "__main__":
    main()
