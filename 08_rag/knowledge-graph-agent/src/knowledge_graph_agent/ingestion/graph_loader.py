"""Neptune Analytics graph loader.

Wraps ``boto3.client('neptune-graph').execute_query`` with openCypher
MERGE semantics so writes are idempotent. The book's audience should be
able to call ``upsert_node`` and ``upsert_edge`` directly without
hand-writing Cypher.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

import boto3
from tenacity import retry, stop_after_attempt, wait_exponential

from knowledge_graph_agent.config import get_settings
from knowledge_graph_agent.extraction.schema import AnyNode, Edge, NodeLabel

log = logging.getLogger(__name__)


class GraphLoader:
    """Thin client over ``neptune-graph`` data-plane API."""

    def __init__(self, graph_id: str | None = None, region: str | None = None) -> None:
        settings = get_settings()
        self.graph_id = graph_id or settings.graph_id
        if not self.graph_id:
            raise ValueError(
                "Neptune Analytics graph_id not configured. Set KG_GRAPH_ID "
                "or pass graph_id= explicitly."
            )
        self.client = boto3.client("neptune-graph", region_name=region or settings.region)

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=0.5, max=8),
        reraise=True,
    )
    def _execute(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        response = self.client.execute_query(
            graphIdentifier=self.graph_id,
            queryString=query,
            parameters=parameters or {},
            language="OPEN_CYPHER",
        )
        payload = response.get("payload")
        if payload is None:
            return []
        if hasattr(payload, "read"):
            payload = payload.read()
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return []
        if isinstance(payload, dict):
            return payload.get("results", []) or []
        return []

    # ── Writes ───────────────────────────────────────────────────────────

    def upsert_node(self, node: AnyNode) -> None:
        props = node.to_properties()
        # Cypher cannot bind a dynamic label, so we whitelist via the enum.
        label = NodeLabel(node.label).value
        query = f"MERGE (n:{label} {{id: $id}}) SET n += $props RETURN n.id AS id"
        self._execute(query, {"id": props["id"], "props": props})
        log.debug("upserted node %s:%s", label, props["id"])

    def upsert_edge(self, edge: Edge) -> None:
        query = (
            f"MATCH (a {{id: $from}}), (b {{id: $to}}) "
            f"MERGE (a)-[r:{edge.type.value}]->(b) SET r += $props RETURN type(r)"
        )
        self._execute(
            query,
            {"from": edge.from_id, "to": edge.to_id, "props": edge.properties},
        )
        log.debug("upserted edge %s -[%s]-> %s", edge.from_id, edge.type.value, edge.to_id)

    def upsert_nodes(self, nodes: Iterable[AnyNode]) -> int:
        count = 0
        for n in nodes:
            self.upsert_node(n)
            count += 1
        return count

    def upsert_edges(self, edges: Iterable[Edge]) -> int:
        count = 0
        for e in edges:
            self.upsert_edge(e)
            count += 1
        return count

    # ── Reads ────────────────────────────────────────────────────────────

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        results = self._execute(
            "MATCH (n {id: $id}) RETURN n LIMIT 1", {"id": node_id}
        )
        if not results:
            return None
        record = results[0]
        return record.get("n") or record

    def execute_cypher(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Escape hatch for callers that need a custom openCypher query."""
        return self._execute(query, parameters)

    def count_nodes(self, label: NodeLabel | None = None) -> int:
        if label is None:
            results = self._execute("MATCH (n) RETURN count(n) AS c")
        else:
            results = self._execute(f"MATCH (n:{label.value}) RETURN count(n) AS c")
        if not results:
            return 0
        return int(results[0].get("c", 0))
