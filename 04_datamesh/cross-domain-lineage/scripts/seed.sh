#!/bin/bash
# Seed the demo mesh with assets, listings, a subscription request, and a
# couple of OpenLineage events so cross-domain-lineage has something to read.
# Usage: ./scripts/seed.sh [profile] [domain-name]
set -e
PROFILE="${1:-default}"
DOMAIN_NAME="${2:-chocolate-mesh}"
REGION="${AWS_REGION:-us-east-1}"

cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi

PYTHONPATH="src" python -m xdl seed --profile "$PROFILE" --region "$REGION" --domain-name "$DOMAIN_NAME"
