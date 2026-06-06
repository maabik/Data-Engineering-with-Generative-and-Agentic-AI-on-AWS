"""Vector search wrapper.

Thin facade over the vector store so callers don't have to worry
whether S3 Vectors or OpenSearch is configured.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from knowledge_graph_agent.extraction._bedrock import embed
from knowledge_graph_agent.ingestion.vector_store import VectorStore, make_vector_store


@dataclass
class VectorHit:
    embedding_id: str
    node_id: str
    name: str
    label: str
    distance: float
    metadata: dict


class VectorSearch:
    def __init__(self, store: VectorStore | None = None) -> None:
        self.store = store or make_vector_store()

    def search_text(self, query: str, *, top_k: int = 20) -> list[VectorHit]:
        # Use the Nova "retrieval" purpose for query-side embeddings;
        # the document side uses GENERIC_INDEX (the embed() default).
        vector = embed(query, purpose="TEXT_RETRIEVAL")
        return self.search_vector(vector, top_k=top_k)

    def search_vector(self, vector: Sequence[float], *, top_k: int = 20) -> list[VectorHit]:
        raw = self.store.query(vector, top_k=top_k)
        hits: list[VectorHit] = []
        for embedding_id, distance, metadata in raw:
            hits.append(
                VectorHit(
                    embedding_id=embedding_id,
                    node_id=metadata.get("node_id", ""),
                    name=metadata.get("name", ""),
                    label=metadata.get("label", ""),
                    distance=distance,
                    metadata=metadata,
                )
            )
        return hits
