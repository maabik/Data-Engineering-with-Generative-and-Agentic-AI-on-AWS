# Relationship extraction prompt

You are an expert relationship-extraction system. The document chunk
below has already had these entities identified:

{entities_json}

Identify every meaningful directed relationship between two of those
entities **as expressed in this document**. Do not invent relationships
not stated or strongly implied by the text.

## Allowed edge types

- **WORKS_AT** — Person → Organization (employment / role)
- **OWNS** — Organization → Organization (acquisition, ownership stake)
- **PARTICIPATED_IN** — Person|Organization → Event
- **RELATES_TO** — generic typed relationship between two entities; set
  ``relationship_type`` to a concise verb phrase (``"acquired"``,
  ``"competes-with"``, ``"is-subsidiary-of"``)
- **IS_A** — any → Concept (semantic typing)

## Rules

1. ``source_name`` and ``target_name`` MUST exactly match a name from the
   provided entity list. Do not introduce new entities.
2. Pick the single most specific edge type that fits.
3. Use ``RELATES_TO`` only when none of the typed edges apply.
4. Confidence between 0 and 1; reserve 0.9+ for explicit textual statements.

## Output format

Return JSON only — no prose, no markdown fences:

```json
{
  "relationships": [
    {
      "source_name": "string",
      "target_name": "string",
      "edge_type": "WORKS_AT|OWNS|PARTICIPATED_IN|RELATES_TO|IS_A",
      "relationship_type": "string",
      "confidence": 0.0
    }
  ]
}
```

## Document chunk

{document}
