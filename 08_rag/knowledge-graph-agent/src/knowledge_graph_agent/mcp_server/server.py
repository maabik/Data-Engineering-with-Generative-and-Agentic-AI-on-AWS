#!/usr/bin/env python3
"""KG MCP Server — runs on Bedrock AgentCore Runtime.

Mirrors ``redshift_mcp_server.py``:
  - FastMCP, ``stateless_http=True``, host ``0.0.0.0``
  - tools registered by name
  - launched with ``mcp.run(transport='streamable-http')``
"""

from mcp.server.fastmcp import FastMCP

from knowledge_graph_agent.mcp_server.tools import (
    kg_find_path,
    kg_get_entity,
    kg_search,
    kg_summarize_subgraph,
)


mcp = FastMCP(
    name="knowledge-graph-agent.mcp",
    host="0.0.0.0",
    stateless_http=True,
    instructions="""
# Knowledge-Graph MCP Server

This MCP server exposes a property graph (Amazon Neptune Analytics)
backed by Bedrock embeddings (Amazon Nova) and Claude-extracted entities.

## Tools

### kg_search(query, mode="hybrid")
Search the graph by `query`. Mode:
  - `"hybrid"` (default) — vector recall + 1–2 hop graph context, re-ranked
  - `"vector"` — semantic similarity only, returns raw hits as JSON
  - `"graph"` — label scan when `query` is itself a NodeLabel

### kg_get_entity(entity_id)
Return the full property map of a node by id.

### kg_find_path(source_id, target_id, max_depth=4)
Return the shortest path between two entities, if any.

### kg_summarize_subgraph(entity_id, depth=2)
Return a compact human-readable subgraph summary, suitable for grounding.
"""
)


mcp.tool(name="kg_search")(kg_search)
mcp.tool(name="kg_get_entity")(kg_get_entity)
mcp.tool(name="kg_find_path")(kg_find_path)
mcp.tool(name="kg_summarize_subgraph")(kg_summarize_subgraph)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
