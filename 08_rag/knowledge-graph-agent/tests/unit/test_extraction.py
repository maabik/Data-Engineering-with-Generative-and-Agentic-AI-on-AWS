"""Schema and parsing unit tests — no AWS calls."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from knowledge_graph_agent.extraction._bedrock import parse_json_response, BedrockExtractionError
from knowledge_graph_agent.extraction.schema import (
    EdgeType,
    Entity,
    ExtractedEntity,
    ExtractedRelationship,
    Document,
    NodeLabel,
    Person,
    is_embeddable,
)
from knowledge_graph_agent.extraction.entity_extractor import _normalize_entity, dedupe_extracted
from knowledge_graph_agent.extraction.relationship_extractor import _normalize_relationship


def test_entity_round_trip():
    e = Entity(name="Acme Corp", entity_type="company", confidence=0.91)
    props = e.to_properties()
    assert props["name"] == "Acme Corp"
    assert props["entity_type"] == "company"
    assert "label" not in props


def test_document_default_id_unique():
    a = Document(name="x", source_uri="s3://a", document_type="pe")
    b = Document(name="x", source_uri="s3://a", document_type="pe")
    assert a.id != b.id


def test_person_label_is_fixed():
    p = Person(name="Maria Chen", role="CEO")
    assert p.label == NodeLabel.PERSON


def test_extracted_entity_defaults():
    e = ExtractedEntity(name="Stripe", label=NodeLabel.ORGANIZATION)
    assert 0.0 <= e.confidence <= 1.0


def test_extracted_relationship_default_edge():
    r = ExtractedRelationship(
        source_name="A",
        target_name="B",
        relationship_type="acquired",
    )
    assert r.edge_type == EdgeType.RELATES_TO


def test_extracted_entity_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ExtractedEntity(name="x", label=NodeLabel.ENTITY, garbage=1)


def test_is_embeddable():
    assert is_embeddable("Sequoia Capital")
    assert not is_embeddable("a")
    assert not is_embeddable("123")
    assert not is_embeddable("12-34")
    assert not is_embeddable("")


def test_normalize_entity_unknown_label_falls_back():
    item = {"name": "Foo", "label": "Wizard"}
    out = _normalize_entity(item)
    assert out["label"] == NodeLabel.ENTITY


def test_normalize_relationship_unknown_edge_type_falls_back():
    item = {
        "source_name": "A",
        "target_name": "B",
        "relationship_type": "ate",
        "edge_type": "WIZARDED",
    }
    out = _normalize_relationship(item)
    assert out["edge_type"] == EdgeType.RELATES_TO


def test_dedupe_extracted():
    a = ExtractedEntity(name="Stripe", label=NodeLabel.ORGANIZATION)
    b = ExtractedEntity(name="stripe", label=NodeLabel.ORGANIZATION)
    c = ExtractedEntity(name="Plaid", label=NodeLabel.ORGANIZATION)
    out = dedupe_extracted([a, b, c])
    assert {x.name for x in out} == {"Stripe", "Plaid"}


def test_parse_json_response_strips_fence():
    text = "```json\n{\"entities\": [{\"name\": \"A\"}]}\n```"
    assert parse_json_response(text) == {"entities": [{"name": "A"}]}


def test_parse_json_response_recovers_from_prose():
    text = "Sure! Here's the result:\n{\"entities\": []}\nLet me know."
    assert parse_json_response(text) == {"entities": []}


def test_parse_json_response_raises_on_garbage():
    with pytest.raises(BedrockExtractionError):
        parse_json_response("nothing useful here")
