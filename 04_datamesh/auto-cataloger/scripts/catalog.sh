#!/bin/bash
# Run Bedrock enrichment over every table in the database.
# Usage: ./scripts/catalog.sh [profile] [database]
set -e
PROFILE="${1:-default}"
DATABASE="${2:-autocat_demo}"
REGION="${AWS_REGION:-us-east-1}"

cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi
PYTHONPATH="src" python -m autocat catalog --profile "$PROFILE" --region "$REGION" --database "$DATABASE"
