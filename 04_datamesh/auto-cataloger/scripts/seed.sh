#!/bin/bash
# Upload seed CSVs to S3 and register Glue tables.
# Usage: ./scripts/seed.sh [profile] [database]
set -e
PROFILE="${1:-default}"
DATABASE="${2:-autocat_demo}"
REGION="${AWS_REGION:-us-east-1}"

cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi
PYTHONPATH="src" python -m autocat seed --profile "$PROFILE" --region "$REGION" --database "$DATABASE"
