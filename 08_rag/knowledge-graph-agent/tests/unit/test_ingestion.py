"""Tests for the deduplication and lineage projection modules."""

from __future__ import annotations

import math

from knowledge_graph_agent.ingestion.deduplication import cosine_similarity, find_duplicate
from knowledge_graph_agent.lineage.lineage_to_graph import _dataset_from_event


def test_cosine_similarity_identity():
    v = [1.0, 2.0, 3.0]
    assert math.isclose(cosine_similarity(v, v), 1.0, rel_tol=1e-9)


def test_cosine_similarity_orthogonal():
    assert math.isclose(cosine_similarity([1, 0], [0, 1]), 0.0)


def test_cosine_similarity_zero_vector():
    assert cosine_similarity([0, 0, 0], [1, 2, 3]) == 0.0


def test_find_duplicate_above_threshold():
    candidate = [1.0, 0.0]
    existing = [
        ("id-a", [0.99, 0.01]),
        ("id-b", [0.0, 1.0]),
    ]
    assert find_duplicate(candidate, existing, threshold=0.9) == "id-a"


def test_find_duplicate_below_threshold_returns_none():
    candidate = [1.0, 0.0]
    existing = [("id-a", [0.5, 0.5])]
    assert find_duplicate(candidate, existing, threshold=0.95) is None


def test_dataset_from_event_handles_missing_fields():
    ds = _dataset_from_event({"name": "raw_payments"}, namespace="kg")
    assert ds.name == "raw_payments"
    assert ds.s3_location == ""
    assert ds.table_format == "unknown"
    assert ds.lineage_namespace == "kg"
