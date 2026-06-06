"""MCP tool implementations exposed by the KG MCP server.

Plain functions wrapped by FastMCP in :mod:`knowledge_graph_agent.mcp_server.server`.
Keeping the logic in plain functions makes them unit-testable without
spinning up the MCP transport.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from knowledge_graph_agent.retrieval.graph_search import GraphSearch
from knowledge_graph_agent.retrieval.hybrid_search import HybridSearch
from knowledge_graph_agent.retrieval.prompt_construction import build_rag_context, build_subgraph_summary
from knowledge_graph_agent.retrieval.vector_search import VectorSearch


def _to_json(value: Any) -> str:
    """Serialize Pydantic / Cypher results to JSON the agent can read."""
    return json.dumps(value, default=str, ensure_ascii=False)


def kg_search(query: str, mode: Literal["graph", "vector", "hybrid"] = "hybrid") -> str:
    """Search the knowledge graph for entities relevant to the query.

    Args:
        query: A natural-language question or keyword phrase.
        mode: ``"graph"`` for label-based lookup, ``"vector"`` for
              semantic search, ``"hybrid"`` for the combined retriever.

    Returns:
        A RAG context string suitable for dropping into a Claude prompt.
    """
    if mode == "vector":
        hits = VectorSearch().search_text(query, top_k=10)
        return _to_json(
            [
                {
                    "node_id": h.node_id,
                    "name": h.name,
                    "label": h.label,
                    "distance": h.distance,
                }
                for h in hits
            ]
        )
    if mode == "graph":
        # Naive label scan — useful when the user already names a label.
        from knowledge_graph_agent.extraction.schema import NodeLabel

        try:
            label = NodeLabel(query)
        except ValueError:
            return _to_json({"error": f"unknown label {query!r}"})
        return _to_json(GraphSearch().find_by_label(label))
    hits = HybridSearch().search(query)
    return build_rag_context(query, hits)


def kg_get_entity(entity_id: str) -> str:
    """Return the full property bag for a single entity by id."""
    node = GraphSearch().get_entity(entity_id)
    return _to_json(node) if node is not None else _to_json({"error": "not found"})


def kg_find_path(source_id: str, target_id: str, max_depth: int = 4) -> str:
    """Return the shortest path between two entities, if one exists."""
    paths = GraphSearch().find_path(source_id, target_id, max_depth=max_depth)
    return _to_json(paths)


def kg_summarize_subgraph(entity_id: str, depth: int = 2) -> str:
    """Return a compact human-readable summary of an entity's subgraph."""
    subgraph = GraphSearch().summarize_subgraph(entity_id, depth=depth)
    return build_subgraph_summary(subgraph)
