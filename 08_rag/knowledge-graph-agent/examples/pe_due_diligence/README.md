# PE Due Diligence — Flagship Example

A 15-minute walkthrough that ingests a synthetic dataroom and lets the
agent answer the five questions in `sample_questions.md`.

## Why this example

Vector-only RAG can surface the right paragraph but stumbles on
multi-hop questions like "_Which executives at companies owned by
Sequoia have a fintech background?_" — because answering it requires
walking three relationships (`Sequoia → OWNS → Company`,
`Company ← WORKS_AT ← Person`, `Person ~ fintech background`).

GraphRAG nails it: the graph traversal is exact, and the embedding
covers the unstructured "fintech background" filter at the end.

## Run it

```bash
# 0. Make sure the foundations are deployed and env is set.
cd ../..
bash scripts/bootstrap.sh        # one-time: provisions Neptune Analytics, S3, Cognito
export KG_GRAPH_ID=$(aws ssm get-parameter --name /kg/graph_id --query 'Parameter.Value' --output text)
export KG_S3_VECTORS_BUCKET=$(aws ssm get-parameter --name /kg/vectors_bucket --query 'Parameter.Value' --output text)

# 1. Ingest the dataroom.
python examples/pe_due_diligence/ingest_dataroom.py

# 2. Ask one of the sample questions.
python examples/pe_due_diligence/run_agent.py \
    "What are the top three integration risks for Acme's FinFlow acquisition?"

# 3. Or run all five sample questions at once.
python examples/pe_due_diligence/run_agent.py --all-samples
```

## What's in the dataroom

Eight synthetic markdown documents — 10-K excerpts, executive bios,
deal announcements, an org chart, a Sequoia portfolio sheet, contract
summaries, litigation summaries, and a news roundup. Total content is
under 5KB to keep ingestion under a minute.

The dataroom is intentionally small. A real PE dataroom is 200–2000
documents — see `docs/operating_costs.md` for a measurement of what
that costs.

## Gotchas we hit

- **Entity name collisions.** Two `Sequoia Capital` mentions across
  files become one node only because the embedding-similarity dedup
  fires (`KG_DEDUP_SIMILARITY_THRESHOLD=0.92`). String-match dedup
  would have missed `Sequoia Capital LLC` vs. `Sequoia Capital, LLC`.
- **Confidence noise.** The relationship extractor sometimes emits
  `confidence=0.55` for relationships the surrounding text only weakly
  implies. Filter post-hoc if you need higher precision.
- **Multi-hop ambiguity.** Question 3 ("Maria Chen ↔ Sequoia") has two
  valid paths: the historical "principal at Sequoia" link and the
  current "works at FinFlow ← OWNS ← Sequoia" link. The agent surfaces
  both — that's correct, not a bug.
