# S3 Vectors RAG Demo — Specification

## Overview
A small but production-shaped retrieval pipeline built on Amazon S3 Vectors and
Bedrock Titan Text Embeddings v2. The demo ingests a real corpus (PDF +
Markdown), chunks it with token awareness, embeds in batches, and writes to a
**reusable** vector bucket so successive runs are incremental rather than
destructive.

## What's in the box

```
src/s3vectors_rag/
  config.py         # Dataclass config sourced from env vars
  logging_setup.py  # Structured logging (also quiets botocore)
  loaders.py        # PDF (pypdf), Markdown (heading-split), and TXT loaders
  chunking.py       # Token-aware chunker (tiktoken if available, whitespace fallback)
  embeddings.py     # Titan v2 embedder with retry on throttling
  store.py          # Idempotent S3VectorStore: ensure/put/query/delete/teardown
  ingest.py         # Pipeline: load -> chunk -> embed -> upsert
  cli.py            # `s3v-rag ingest|query|stats|clean`
samples/            # Markdown sources used by the demo
notebooks/          # Jupyter walkthrough
tests/              # pytest with botocore Stubber (no live AWS)
```

## Index configuration
- **Metric**: cosine (configurable via `S3V_DISTANCE`)
- **Dimension**: 1024 by default (Titan v2 supports 256/512/1024)
- **Bucket**: `s3-vectors-rag-demo` (override with `S3V_BUCKET`)
- **Index**: `rag-index` (override with `S3V_INDEX`)

## Metadata schema
Every chunk is stored with:
- `source` — file name or user-supplied `--source-tag`
- `path` — original absolute path
- `locator` — `page=N` for PDFs, `section=Title` for Markdown
- `chunk_idx` — chunk position within its section
- `ingest_ts` — UTC ISO timestamp
- `text` — preview of the chunk (truncated to 1500 chars) so query results are readable

These are filterable at query time:
```
s3v-rag query "..." --filter source=aws-rag.md --filter chunk_idx=0
```

## Demo workflow
1. **Setup** (idempotent): `ensure_bucket` + `ensure_index` — re-running is safe.
2. **Ingest**: load → section → chunk → embed in batches → `put_vectors` in batches.
3. **Query**: embed query → `query_vectors` (with optional metadata filter) → display top-k.
4. **Stats**: `get_index` info.
5. **Clean** (opt-in): `delete_index` then `delete_vector_bucket`.

## Differences from the original spec
- Reusable bucket — the bucket name is no longer randomized per run, so successive
  ingests add to the same index instead of creating and tearing down infrastructure
  each time.
- Real corpus + chunking instead of 5 toy sentences.
- Filterable metadata on every vector.
- Library + CLI shape so the pipeline is testable and embeddable in notebooks.
- pytest coverage with botocore `Stubber` — no live AWS needed for the test suite.

## AWS services & APIs
- **S3 Vectors**: `create_vector_bucket`, `create_index`, `put_vectors`, `query_vectors`,
  `delete_vectors`, `delete_index`, `delete_vector_bucket`, `get_index`
- **Bedrock Runtime**: `invoke_model` against `amazon.titan-embed-text-v2:0`

## Configuration (env vars)
| Variable | Default | Purpose |
| --- | --- | --- |
| `AWS_REGION` | `us-east-1` | Region for both clients |
| `S3V_BUCKET` | `s3-vectors-rag-demo` | Vector bucket name |
| `S3V_INDEX` | `rag-index` | Index name |
| `S3V_EMBED_MODEL` | `amazon.titan-embed-text-v2:0` | Embedding model id |
| `S3V_EMBED_DIM` | `1024` | Embedding/index dimension |
| `S3V_CHUNK_SIZE` | `400` | Chunk size in tokens |
| `S3V_CHUNK_OVERLAP` | `60` | Chunk overlap in tokens |
| `S3V_EMBED_BATCH` | `16` | Embedding batch size |
| `S3V_PUT_BATCH` | `25` | put_vectors batch size |
| `S3V_DISTANCE` | `COSINE` | Index distance metric |
| `S3V_LOG_LEVEL` | `INFO` | Log level |
