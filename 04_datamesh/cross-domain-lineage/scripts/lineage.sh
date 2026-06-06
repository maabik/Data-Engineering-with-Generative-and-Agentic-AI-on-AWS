#!/bin/bash
# Build the cross-domain dependency graph. Usage: ./scripts/lineage.sh [profile] [domain-name]
set -e
PROFILE="${1:-default}"
DOMAIN_NAME="${2:-}"
REGION="${AWS_REGION:-us-east-1}"

cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi

ARGS=(--profile "$PROFILE" --region "$REGION")
if [ -n "$DOMAIN_NAME" ]; then
  ARGS+=(--domain-name "$DOMAIN_NAME")
fi

PYTHONPATH="src" python -m xdl lineage "${ARGS[@]}"
