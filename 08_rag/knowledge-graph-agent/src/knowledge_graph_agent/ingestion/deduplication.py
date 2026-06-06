"""Embedding-based entity deduplication.

The spec is explicit: never deduplicate by string match. Use cosine
similarity over Nova embeddings, threshold defaults to 0.92 and is
configurable via :attr:`Settings.dedup_similarity_threshold`.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

from knowledge_graph_agent.config import get_settings


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vectors must be the same length")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def find_duplicate(
    candidate_embedding: Sequence[float],
    existing: Iterable[tuple[str, Sequence[float]]],
    threshold: float | None = None,
) -> str | None:
    """Return the id of the existing entity whose cosine ≥ threshold, else None."""

    thr = threshold if threshold is not None else get_settings().dedup_similarity_threshold
    best_id: str | None = None
    best_score = thr
    for entity_id, vec in existing:
        score = cosine_similarity(candidate_embedding, vec)
        if score >= best_score:
            best_score = score
            best_id = entity_id
    return best_id
