# s3_vectors — RAG demo on Amazon S3 Vectors

A small RAG ingest/retrieval pipeline built on Amazon S3 Vectors and Bedrock
Titan Text Embeddings v2. Originally a single 150-line script over 5 toy
sentences; now a small library + CLI with real loaders, token-aware chunking,
metadata filtering, idempotent setup, and a pytest suite.

## Install

```bash
python -m venv s3_vectors_env
source s3_vectors_env/bin/activate
pip install -e ".[all]"
# or, if you don't want the package install:
pip install -r requirements.txt && export PYTHONPATH=src
```

You'll need AWS credentials with access to the `s3vectors` and `bedrock-runtime`
APIs in a region where both are available.

## Quick start

```bash
# 1. Ingest the bundled samples (idempotent — safe to re-run).
python -m s3vectors_rag.cli ingest samples

# 2. Query.
python -m s3vectors_rag.cli query "How do I store and query vectors in S3?" --top-k 3

# 3. Filter by metadata.
python -m s3vectors_rag.cli query "embedding dimensions" --filter source=aws-rag.md

# 4. Inspect the index.
python -m s3vectors_rag.cli stats

# 5. Tear it down when you're done.
python -m s3vectors_rag.cli clean --yes
```

The bundled `run_demo.sh` runs ingest + a couple of queries + stats end-to-end.
Set `CLEAN=1` to also tear the bucket down at the end.

## Ingesting your own corpus

```bash
python -m s3vectors_rag.cli ingest path/to/docs --source-tag prod-docs
```

Supported formats:
- `*.pdf` — text extracted per page; locator is `page=N`
- `*.md` / `*.markdown` — split on `#` and `##` headings; locator is `section=Title`
- `*.txt` — single section

The bucket name is **not** randomized — successive ingests add to the same
index. Override with `S3V_BUCKET` or `--bucket` if you want isolation.

## Configuration

All settings can be overridden by environment variable; see
`s3_vectors_demo_spec.md` for the full table.

## Notebook

`notebooks/s3_vectors_rag_walkthrough.ipynb` walks through the same pipeline
interactively — useful for course/book contexts where you want to inspect
chunks and scores cell by cell.

## Tests

```bash
pip install -e ".[test]"
pytest
```

Tests use botocore's `Stubber` so they don't hit AWS.
