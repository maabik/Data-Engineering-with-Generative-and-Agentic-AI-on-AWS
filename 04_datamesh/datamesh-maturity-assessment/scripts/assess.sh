#!/bin/bash
# Run the assessment against a DataZone domain in the given AWS profile.
# Usage:
#   ./scripts/assess.sh                              # auto-pick the only domain
#   ./scripts/assess.sh default                      # specify profile
#   ./scripts/assess.sh default chocolate-mesh       # specify profile + domain name
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
PYTHONPATH="src" python -m dmma "${ARGS[@]}"
