"""Exercise the SigV4 transport end-to-end without invoking Bedrock.

Calls `initialize`, `tools/list`, and `tools/call whoami` on the Lambda MCP
target through the bridge. Succeeds if IAM accepts the signatures and the
Lambda returns a non-empty caller ARN.
"""
from __future__ import annotations

import os

from sigv4_mcp_bridge import strands_mcp_client

MCP_URL = os.environ["MCP_FUNCTION_URL"].rstrip("/") + "/mcp"
REGION = os.environ.get("AWS_REGION") or os.environ["MCP_REGION"]


def main() -> None:
    mcp = strands_mcp_client(url=MCP_URL, service="lambda", region=REGION)
    with mcp:
        tools = mcp.list_tools_sync()
        print(f"tools/list: {[t.tool_name for t in tools]}")

        result = mcp.call_tool_sync(tool_use_id="smoke-1", name="whoami", arguments={})
        print(f"whoami: {result}")


if __name__ == "__main__":
    main()
