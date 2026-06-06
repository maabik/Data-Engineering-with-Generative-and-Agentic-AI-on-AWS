# Extraction patterns

## Two-pass extraction

Entities first, relationships second. Why:

- The relationship pass receives the entity list as a vocabulary, which
  stops Claude from hallucinating new entities mid-extraction.
- Entity extraction can run on cheaper models when token-budget matters;
  relationship extraction benefits from Claude 3.7 Sonnet's reasoning.
- The two passes are independently retry-safe (`tenacity` exponential
  backoff in `_bedrock.py`).

## Externalized prompts

Prompts live as plain markdown under `src/knowledge_graph_agent/extraction/prompts/`.
This is non-negotiable per the spec. Iterating on a prompt should be a
text edit, not a code change.

When adding a new prompt:

1. Drop the file in `prompts/` with a descriptive slug.
2. Reference it via `_bedrock.load_prompt("your_slug")`.
3. Use `{token}` placeholders for fields you'll inject — Python's
   `str.replace` handles them, no f-string surprises.

## Confidence thresholds

The default extractor accepts everything Claude returns. Filter at the
ingestion layer if you want a higher-precision graph:

```python
extracted = [e for e in extracted if e.confidence >= 0.8]
```

A common mistake is to filter too early — vector search can recover
plausible-but-low-confidence entities that the agent later confirms via
a graph traversal. Err on the side of letting things through.

## Embeddable name guard

Entities are only embedded when `is_embeddable(name)` is true:
length ≥ `KG_EMBEDDABLE_MIN_NAME_LENGTH` (default 4) and not numeric.
Tweak when ingesting domains where short names are meaningful (ticker
symbols, ISO codes).

## Deduplication

`knowledge_graph_agent.ingestion.deduplication.find_duplicate` does cosine-similarity
matching above `KG_DEDUP_SIMILARITY_THRESHOLD` (default 0.92).
Lower the threshold for noisy domains; raise it when you want strict
identity.

The current implementation keeps an in-memory window of recent
embeddings. For production, swap in a vector-store query — the
`VectorStore` interface already supports nearest-neighbor lookup.
