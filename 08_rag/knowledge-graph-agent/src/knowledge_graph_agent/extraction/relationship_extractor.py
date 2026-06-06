"""Bedrock-powered relationship extraction.

Two-pass design — entities first, relationships second — gives the model
a known vocabulary and stops it from hallucinating new entities while
extracting relationships.
"""

from __future__ import annotations

import json
import logging
from typing import Sequence

from pydantic import ValidationError

from knowledge_graph_agent.extraction._bedrock import converse, load_prompt, parse_json_response
from knowledge_graph_agent.extraction.schema import EdgeType, ExtractedEntity, ExtractedRelationship

log = logging.getLogger(__name__)


def extract_relationships(
    document: str,
    entities: Sequence[ExtractedEntity],
) -> tuple[list[ExtractedRelationship], dict[str, int]]:
    """Extract directed relationships between the supplied entities.

    Drops any relationship whose endpoints don't match an entity name.
    """
    if len(entities) < 2:
        return [], {"input": 0, "output": 0, "total": 0}

    entity_payload = json.dumps(
        [{"name": e.name, "label": e.label.value, "entity_type": e.entity_type} for e in entities],
        indent=2,
    )
    prompt = (
        load_prompt("relationship_extraction")
        .replace("{entities_json}", entity_payload)
        .replace("{document}", document)
    )
    text, usage = converse(prompt)
    payload = parse_json_response(text)
    raw = payload.get("relationships", [])
    valid_names = {e.name.casefold() for e in entities}

    relationships: list[ExtractedRelationship] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            rel = ExtractedRelationship(**_normalize_relationship(item))
        except ValidationError as exc:
            log.warning("dropping invalid relationship %r: %s", item, exc)
            continue
        if rel.source_name.casefold() not in valid_names:
            log.warning("dropping rel with unknown source %r", rel.source_name)
            continue
        if rel.target_name.casefold() not in valid_names:
            log.warning("dropping rel with unknown target %r", rel.target_name)
            continue
        if rel.source_name == rel.target_name:
            continue
        relationships.append(rel)
    return relationships, usage


def _normalize_relationship(item: dict) -> dict:
    item = dict(item)
    edge_type = item.get("edge_type", EdgeType.RELATES_TO)
    if isinstance(edge_type, str):
        try:
            edge_type = EdgeType(edge_type)
        except ValueError:
            edge_type = EdgeType.RELATES_TO
    item["edge_type"] = edge_type
    # Claude occasionally emits relationship_type=null when the edge
    # type is itself the relation (e.g. WORKS_AT). Fall back to the
    # edge_type's lowercase form so the property is always non-null.
    rt = item.get("relationship_type")
    if not isinstance(rt, str) or not rt.strip():
        item["relationship_type"] = edge_type.value.lower().replace("_", "-")
    return item
