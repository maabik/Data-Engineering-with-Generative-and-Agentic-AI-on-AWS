"""Document → graph batch loader.

Glues the extractors, embedder, vector store, and graph loader together
into a single ``process_document`` entrypoint that the Step Functions
Lambda invokes (and that local scripts can call directly).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlparse
from uuid import uuid4

import boto3

from knowledge_graph_agent.config import get_settings
from knowledge_graph_agent.extraction._bedrock import embed
from knowledge_graph_agent.extraction.entity_extractor import dedupe_extracted, extract_entities
from knowledge_graph_agent.extraction.relationship_extractor import extract_relationships
from knowledge_graph_agent.extraction.schema import (
    Document,
    Edge,
    EdgeType,
    Entity,
    ExtractedEntity,
    ExtractedRelationship,
    NodeLabel,
    Organization,
    Person,
    Concept,
    Event,
    is_embeddable,
)
from knowledge_graph_agent.ingestion.deduplication import find_duplicate
from knowledge_graph_agent.ingestion.graph_loader import GraphLoader
from knowledge_graph_agent.ingestion.vector_store import VectorStore, make_vector_store

log = logging.getLogger(__name__)


@dataclass
class IngestionStats:
    document_id: str
    entities_added: int = 0
    entities_deduped: int = 0
    relationships_added: int = 0
    embeddings_written: int = 0
    token_usage: dict[str, int] = field(default_factory=dict)


def _read_s3_text(s3_uri: str) -> str:
    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"not an s3:// URI: {s3_uri!r}")
    s3 = boto3.client("s3", region_name=get_settings().region)
    response = s3.get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))
    return response["Body"].read().decode("utf-8", errors="replace")


def _node_for(extracted: ExtractedEntity) -> Document | Entity | Concept | Person | Organization | Event:
    """Promote an ExtractedEntity to its concrete typed node."""
    name = extracted.name.strip()
    if extracted.label == NodeLabel.PERSON:
        return Person(name=name, role=extracted.entity_type)
    if extracted.label == NodeLabel.ORGANIZATION:
        return Organization(name=name, org_type=extracted.entity_type or "company")
    if extracted.label == NodeLabel.EVENT:
        return Event(name=name, event_type=extracted.entity_type or "event", description=extracted.description)
    if extracted.label == NodeLabel.CONCEPT:
        return Concept(name=name, definition=extracted.description or name)
    return Entity(
        name=name,
        entity_type=extracted.entity_type or "thing",
        description=extracted.description,
        confidence=extracted.confidence,
    )


def _edge_for(
    rel: ExtractedRelationship,
    name_to_id: dict[str, str],
    document_id: str,
) -> Edge | None:
    src = name_to_id.get(rel.source_name.casefold())
    dst = name_to_id.get(rel.target_name.casefold())
    if not src or not dst:
        return None
    return Edge(
        type=rel.edge_type,
        from_id=src,
        to_id=dst,
        properties={
            "relationship_type": rel.relationship_type,
            "confidence": rel.confidence,
            "source_doc_id": document_id,
        },
    )


def _embed_and_store(
    name: str,
    description: str | None,
    embedding_id: str,
    metadata: dict,
    vector_store: VectorStore,
) -> list[float]:
    text = name
    if description:
        text = f"{name}: {description}"
    vector = embed(text)
    vector_store.upsert(embedding_id, vector, metadata)
    return vector


def process_document(
    s3_uri: str,
    document_type: str = "unknown",
    *,
    title: str | None = None,
    chunk: str | None = None,
    graph_loader: GraphLoader | None = None,
    vector_store: VectorStore | None = None,
) -> IngestionStats:
    """Run the full extraction pipeline for one document.

    If ``chunk`` is provided it overrides the S3 fetch (useful for tests
    and local examples).
    """
    settings = get_settings()
    text = chunk if chunk is not None else _read_s3_text(s3_uri)
    if not text.strip():
        raise ValueError(f"document at {s3_uri!r} is empty")

    document = Document(
        source_uri=s3_uri,
        document_type=document_type,
        title=title,
        ingested_at=datetime.now(timezone.utc),
        name=title or s3_uri,
    )
    stats = IngestionStats(document_id=document.id)

    extracted, ent_usage = extract_entities(text, min_name_length=settings.embeddable_min_name_length)
    extracted = dedupe_extracted(extracted)
    rels, rel_usage = extract_relationships(text, extracted)
    stats.token_usage = {
        "input": ent_usage.get("input", 0) + rel_usage.get("input", 0),
        "output": ent_usage.get("output", 0) + rel_usage.get("output", 0),
        "total": ent_usage.get("total", 0) + rel_usage.get("total", 0),
    }

    loader = graph_loader or GraphLoader()
    store = vector_store or make_vector_store()
    loader.upsert_node(document)

    name_to_id: dict[str, str] = {}
    for ext in extracted:
        node = _node_for(ext)
        if is_embeddable(node.name, settings.embeddable_min_name_length):
            embedding_id = uuid4().hex
            try:
                vector = _embed_and_store(
                    node.name,
                    getattr(node, "description", None),
                    embedding_id,
                    {
                        "node_id": node.id,
                        "label": node.label.value,
                        "name": node.name,
                    },
                    store,
                )
                stats.embeddings_written += 1

                # Embedding-based dedup against already-loaded entities.
                # Production deployments should query the vector store
                # for an existing match instead of carrying an in-memory
                # registry; we keep the registry here so the example is
                # self-contained.
                duplicate_id = find_duplicate(
                    vector,
                    _RECENT_EMBEDDINGS,
                    threshold=settings.dedup_similarity_threshold,
                )
                if duplicate_id is not None:
                    log.info("entity %r deduped against %s", node.name, duplicate_id)
                    name_to_id[ext.name.casefold()] = duplicate_id
                    stats.entities_deduped += 1
                    continue

                node.embedding_id = embedding_id
                _RECENT_EMBEDDINGS.append((node.id, vector))
            except Exception as exc:
                log.warning("embedding failed for %r: %s", node.name, exc)

        loader.upsert_node(node)
        name_to_id[ext.name.casefold()] = node.id
        stats.entities_added += 1

        loader.upsert_edge(
            Edge(
                type=EdgeType.MENTIONS,
                from_id=document.id,
                to_id=node.id,
                properties={
                    "confidence": ext.confidence,
                    "position_in_doc": ext.position_in_doc or 0,
                },
            )
        )

    for rel in rels:
        edge = _edge_for(rel, name_to_id, document.id)
        if edge is None:
            continue
        loader.upsert_edge(edge)
        stats.relationships_added += 1

    return stats


# Tiny in-memory window of recent embeddings used by the dedup pass. This
# keeps the example simple; for production, swap in a vector-store query
# (the store already supports nearest-neighbor lookup).
_RECENT_EMBEDDINGS: list[tuple[str, list[float]]] = []


def process_documents(
    documents: Iterable[dict],
    *,
    graph_loader: GraphLoader | None = None,
    vector_store: VectorStore | None = None,
) -> list[IngestionStats]:
    """Convenience wrapper: each item is a dict with s3_uri / document_type / title."""
    loader = graph_loader or GraphLoader()
    store = vector_store or make_vector_store()
    out: list[IngestionStats] = []
    for d in documents:
        out.append(
            process_document(
                s3_uri=d["s3_uri"],
                document_type=d.get("document_type", "unknown"),
                title=d.get("title"),
                chunk=d.get("chunk"),
                graph_loader=loader,
                vector_store=store,
            )
        )
    return out
