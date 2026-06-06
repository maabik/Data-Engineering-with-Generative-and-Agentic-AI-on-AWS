"""Turn retrieval results into compact RAG context strings.

Designed for the Strands agent to drop into a Claude prompt as
``Context``. The format favors readability over token efficiency — at
the scale this targets, a few hundred extra tokens beats an LLM that
can't follow citations.
"""

from __future__ import annotations

from typing import Any, Iterable

from knowledge_graph_agent.retrieval.hybrid_search import HybridHit


def _node_props(node: Any) -> dict[str, Any]:
    """Neptune Analytics returns ``{'~labels':..., '~properties':{...}}``;
    in-process tests pass plain dicts. Normalize to a single property bag.
    """
    if not isinstance(node, dict):
        return {}
    if "~properties" in node:
        props = dict(node.get("~properties") or {})
        labels = node.get("~labels") or []
        if labels and "label" not in props:
            props["label"] = labels[0]
        return props
    return node


def _format_node(node: Any) -> str:
    props = _node_props(node)
    if not props:
        return str(node)
    name = props.get("name") or props.get("canonical_name") or props.get("id") or "(unnamed)"
    label = props.get("label") or "Entity"
    description = props.get("description")
    if description:
        return f"- {label}: {name} — {description}"
    return f"- {label}: {name}"


def _format_edge(edge: Any) -> str:
    if isinstance(edge, list):
        # The Neptune response sometimes wraps edges in single-element lists
        edge = edge[0] if edge else {}
    if not isinstance(edge, dict):
        return f"  {edge}"
    if "~properties" in edge or "~type" in edge:
        edge_type = edge.get("~type") or "RELATES_TO"
        props = edge.get("~properties") or {}
        rel_type = props.get("relationship_type") or edge_type
        return f"  -[{edge_type} ({rel_type})]->"
    edge_type = edge.get("type") or edge.get("relationship_type") or "RELATES_TO"
    src = edge.get("from") or edge.get("source") or "?"
    dst = edge.get("to") or edge.get("target") or "?"
    return f"  ({src}) -[{edge_type}]-> ({dst})"


def build_rag_context(
    query: str,
    hits: Iterable[HybridHit],
    *,
    max_neighbors_per_hit: int = 8,
) -> str:
    """Render a hybrid retrieval result as a RAG context string."""
    sections = [f"# Knowledge Graph Context for: {query}"]
    for i, hit in enumerate(hits, 1):
        sections.append(f"\n## {i}. {hit.label}: {hit.name} (score={hit.score:.2f})")
        sections.append(f"  node_id: {hit.node_id}")
        if hit.graph_neighbors:
            sections.append("  related entities:")
            for neighbor in hit.graph_neighbors[:max_neighbors_per_hit]:
                sections.append("  " + _format_node(neighbor))
        if hit.graph_edges:
            sections.append("  edges:")
            for edge in hit.graph_edges[:max_neighbors_per_hit]:
                sections.append(_format_edge(edge))

    sections.append(
        "\n# Instructions"
        "\nAnswer the user's question using only the entities and edges above."
        "\nWhen citing, refer to entities by name. If the context does not"
        "\ncontain enough information, say so plainly — do not speculate."
    )
    return "\n".join(sections)


def build_subgraph_summary(subgraph: dict[str, Any]) -> str:
    """Render a single subgraph (from ``GraphSearch.summarize_subgraph``)."""
    entity = subgraph.get("entity")
    neighbors = subgraph.get("neighbors", [])
    edges = subgraph.get("edges", [])
    if not entity:
        return "(empty subgraph)"
    sections = [_format_node(entity)]
    if neighbors:
        sections.append("\nNeighbors:")
        sections.extend(_format_node(n) for n in neighbors)
    if edges:
        sections.append("\nEdges:")
        sections.extend(_format_edge(e) for e in edges)
    return "\n".join(sections)
