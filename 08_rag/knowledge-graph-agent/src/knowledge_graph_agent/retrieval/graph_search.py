"""openCypher traversal queries against Neptune Analytics."""

from __future__ import annotations

from typing import Any, Sequence

from knowledge_graph_agent.extraction.schema import EdgeType, NodeLabel
from knowledge_graph_agent.ingestion.graph_loader import GraphLoader


class GraphSearch:
    """High-level graph queries the agent and the MCP server expose."""

    def __init__(self, loader: GraphLoader | None = None) -> None:
        self.loader = loader or GraphLoader()

    # ── Lookups ─────────────────────────────────────────────────────────

    def get_entity(self, entity_id: str) -> dict[str, Any] | None:
        return self.loader.get_node(entity_id)

    def find_by_label(
        self,
        label: NodeLabel,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 25,
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        where = " AND ".join(f"n.{k} = ${k}" for k in filters)
        where_clause = f"WHERE {where}" if where else ""
        query = f"MATCH (n:{label.value}) {where_clause} RETURN n LIMIT {int(limit)}"
        results = self.loader.execute_cypher(query, filters)
        return [r.get("n", r) for r in results]

    # ── Traversals ──────────────────────────────────────────────────────

    def find_neighbors(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        edge_types: Sequence[EdgeType] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if depth < 1:
            raise ValueError("depth must be >= 1")
        edge_filter = ""
        if edge_types:
            edge_filter = ":" + "|".join(e.value for e in edge_types)
        # Bound depth at the call site, not in the user-supplied value.
        depth = max(1, min(int(depth), 4))
        query = (
            f"MATCH (n {{id: $id}})-[r{edge_filter}*1..{depth}]->(m) "
            f"RETURN m, r LIMIT {int(limit)}"
        )
        return self.loader.execute_cypher(query, {"id": entity_id})

    def find_path(
        self,
        source_id: str,
        target_id: str,
        *,
        max_depth: int = 4,
    ) -> list[dict[str, Any]]:
        max_depth = max(1, min(int(max_depth), 6))
        query = (
            f"MATCH p = shortestPath((a {{id: $src}})-[*1..{max_depth}]-(b {{id: $dst}})) "
            f"RETURN p LIMIT 1"
        )
        return self.loader.execute_cypher(query, {"src": source_id, "dst": target_id})

    # ── Subgraph summary ────────────────────────────────────────────────

    def summarize_subgraph(self, entity_id: str, *, depth: int = 2, limit: int = 100) -> dict[str, Any]:
        """Return a compact subgraph: the entity, its neighbors, and the connecting edges."""
        depth = max(1, min(int(depth), 3))
        query = (
            f"MATCH (n {{id: $id}})-[r*1..{depth}]-(m) "
            f"RETURN n, collect(DISTINCT m) AS neighbors, collect(DISTINCT r) AS edges "
            f"LIMIT {int(limit)}"
        )
        results = self.loader.execute_cypher(query, {"id": entity_id})
        if not results:
            return {"entity": None, "neighbors": [], "edges": []}
        first = results[0]
        return {
            "entity": first.get("n"),
            "neighbors": first.get("neighbors", []),
            "edges": first.get("edges", []),
        }
