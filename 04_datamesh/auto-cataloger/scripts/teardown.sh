#!/bin/bash
# Delete the Glue database (cascading to its tables) and empty + remove
# the seed S3 bucket.
# Usage: ./scripts/teardown.sh [profile] [database]
set -e
PROFILE="${1:-default}"
DATABASE="${2:-autocat_demo}"
REGION="${AWS_REGION:-us-east-1}"

cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi
PYTHONPATH="src" python -m autocat destroy --profile "$PROFILE" --region "$REGION" --database "$DATABASE"
