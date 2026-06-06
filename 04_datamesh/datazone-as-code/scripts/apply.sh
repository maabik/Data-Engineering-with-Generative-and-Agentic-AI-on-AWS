#!/bin/bash
# Apply the example manifest. Usage: ./scripts/apply.sh [profile] [manifest]
set -e
PROFILE="${1:-default}"
MANIFEST="${2:-manifests/example.yaml}"
REGION="${AWS_REGION:-us-east-1}"

cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi
PYTHONPATH="src" python -m dzac apply --manifest "$MANIFEST" --profile "$PROFILE" --region "$REGION"
