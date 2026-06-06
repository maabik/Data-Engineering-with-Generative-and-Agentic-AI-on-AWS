"""Hybrid graph + vector retrieval with re-ranking.

Algorithm (matches the spec, Phase 3):

1. Vector search returns top-K semantically similar nodes.
2. For each result, traverse the graph 1-2 hops to find related entities.
3. Re-rank by combined score = (vector similarity) + (graph proximity weight).
4. Return the top-N nodes with their context subgraphs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from knowledge_graph_agent.config import get_settings
from knowledge_graph_agent.retrieval.graph_search import GraphSearch
from knowledge_graph_agent.retrieval.vector_search import VectorHit, VectorSearch


@dataclass
class HybridHit:
    node_id: str
    name: str
    label: str
    score: float
    vector_distance: float
    graph_neighbors: list[dict[str, Any]]
    graph_edges: list[Any]


class HybridSearch:
    """Combine semantic recall (vectors) with structural relevance (graph)."""

    def __init__(
        self,
        graph: GraphSearch | None = None,
        vector: VectorSearch | None = None,
    ) -> None:
        self.graph = graph or GraphSearch()
        self.vector = vector or VectorSearch()

    def search(
        self,
        query: str,
        *,
        top_k_vector: int | None = None,
        traversal_depth: int | None = None,
        top_n: int | None = None,
        graph_weight: float = 0.4,
    ) -> list[HybridHit]:
        settings = get_settings()
        top_k = top_k_vector or settings.default_top_k_vector
        depth = traversal_depth or settings.default_traversal_depth
        top_n = top_n or settings.default_top_n_hybrid

        vector_hits = self.vector.search_text(query, top_k=top_k)
        if not vector_hits:
            return []

        # Smaller distance = closer match. Normalize to a similarity in [0, 1].
        max_distance = max((h.distance for h in vector_hits), default=1.0) or 1.0
        ranked: list[HybridHit] = []
        for hit in vector_hits:
            vector_similarity = max(0.0, 1.0 - (hit.distance / max_distance))
            subgraph = self.graph.summarize_subgraph(hit.node_id, depth=depth)
            neighbors = subgraph.get("neighbors", []) or []
            graph_proximity = min(1.0, len(neighbors) / 10.0)
            score = (1.0 - graph_weight) * vector_similarity + graph_weight * graph_proximity
            ranked.append(
                HybridHit(
                    node_id=hit.node_id,
                    name=hit.name,
                    label=hit.label,
                    score=score,
                    vector_distance=hit.distance,
                    graph_neighbors=neighbors,
                    graph_edges=subgraph.get("edges", []) or [],
                )
            )

        ranked.sort(key=lambda h: h.score, reverse=True)
        return ranked[:top_n]


def _vector_hit_to_dict(hit: VectorHit) -> dict[str, Any]:
    return {
        "node_id": hit.node_id,
        "name": hit.name,
        "label": hit.label,
        "distance": hit.distance,
    }
