#!/bin/bash
# Validate and (only if clean) publish the asset as a DataZone listing.
# Usage: ./scripts/publish.sh [profile] [contract.yaml] [extra-args]
set -e
PROFILE="${1:-default}"
CONTRACT="${2:-contracts/cocoa_shipments.yaml}"
shift 2 || true
REGION="${AWS_REGION:-us-east-1}"

cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi
PYTHONPATH="src" python -m dpv publish --contract "$CONTRACT" --profile "$PROFILE" --region "$REGION" "$@"
