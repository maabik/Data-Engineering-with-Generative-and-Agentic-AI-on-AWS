"""Bedrock-powered entity extraction.

Reads the markdown prompt at ``prompts/entity_extraction.md``, fills in
the document chunk, calls Claude via the Converse API, and returns a
list of validated :class:`ExtractedEntity` objects.
"""

from __future__ import annotations

import logging
from typing import Iterable

from pydantic import ValidationError

from knowledge_graph_agent.extraction._bedrock import converse, load_prompt, parse_json_response
from knowledge_graph_agent.extraction.schema import ExtractedEntity, NodeLabel, is_embeddable

log = logging.getLogger(__name__)


def extract_entities(
    document: str,
    *,
    min_name_length: int = 4,
) -> tuple[list[ExtractedEntity], dict[str, int]]:
    """Extract entities from a single document chunk.

    Returns ``(entities, token_usage)``. Entities below the embeddable
    name threshold are dropped — the spec is explicit that we should not
    embed numeric or single-word fragments.
    """
    prompt = load_prompt("entity_extraction").replace("{document}", document)
    text, usage = converse(prompt)
    payload = parse_json_response(text)
    raw = payload.get("entities", [])

    entities: list[ExtractedEntity] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            entity = ExtractedEntity(**_normalize_entity(item))
        except ValidationError as exc:
            log.warning("dropping invalid entity %r: %s", item, exc)
            continue
        if not is_embeddable(entity.name, min_len=min_name_length):
            log.debug("dropping unembeddable entity %r", entity.name)
            continue
        entities.append(entity)

    return entities, usage


def _normalize_entity(item: dict) -> dict:
    """Coerce a few common label spellings the model emits."""
    label = item.get("label", "Entity")
    if isinstance(label, str):
        try:
            label = NodeLabel(label)
        except ValueError:
            label = NodeLabel.ENTITY
    item = dict(item)
    item["label"] = label
    return item


def dedupe_extracted(entities: Iterable[ExtractedEntity]) -> list[ExtractedEntity]:
    """Drop later occurrences with the same lowercase name (per-chunk only).

    Cross-chunk and cross-document deduplication uses embedding cosine
    similarity (see :mod:`knowledge_graph_agent.ingestion.deduplication`).
    """
    seen: set[str] = set()
    out: list[ExtractedEntity] = []
    for e in entities:
        key = e.name.casefold().strip()
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out
