#!/usr/bin/env bash
set -euo pipefail

# Activate the bundled venv if present; otherwise rely on the caller's env.
if [ -f "s3_vectors_env/bin/activate" ]; then
  # shellcheck disable=SC1091
  source s3_vectors_env/bin/activate
fi

export PYTHONPATH="${PYTHONPATH:-}:src"

echo "==> ingesting samples/"
python -m s3vectors_rag.cli ingest samples

echo
echo "==> querying"
python -m s3vectors_rag.cli query "How do I store and query vectors in S3?" --top-k 3

echo
echo "==> querying with metadata filter (source=aws-rag.md)"
python -m s3vectors_rag.cli query "what does Titan v2 produce?" --top-k 3 --filter source=aws-rag.md

echo
echo "==> stats"
python -m s3vectors_rag.cli stats || true

if [ "${CLEAN:-0}" = "1" ]; then
  echo
  echo "==> cleaning"
  python -m s3vectors_rag.cli clean --yes
fi
