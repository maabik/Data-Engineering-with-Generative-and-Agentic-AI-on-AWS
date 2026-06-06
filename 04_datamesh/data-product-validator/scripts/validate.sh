#!/bin/bash
# Usage: ./scripts/validate.sh [profile] [contract.yaml]
set -e
PROFILE="${1:-default}"
CONTRACT="${2:-contracts/cocoa_shipments.yaml}"
REGION="${AWS_REGION:-us-east-1}"

cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi
PYTHONPATH="src" python -m dpv validate --contract "$CONTRACT" --profile "$PROFILE" --region "$REGION"
