#!/bin/bash
# Tear down everything created by apply.sh: deletes the domain (with cascade)
# and the auto-created domain execution role.
#
# Usage: ./scripts/teardown.sh [profile] [manifest]
#
# DataZone delete-domain with skipDeletionCheck=true cascades through projects,
# domain units, glossaries, terms, environments, and grants. The IAM role is
# only removed in the producer side (where it was auto-created); pass --keep-role
# to leave it.
set -e
PROFILE="${1:-default}"
MANIFEST="${2:-manifests/example.yaml}"
REGION="${AWS_REGION:-us-east-1}"

cd "$(dirname "$0")/.."
if [ -d .venv ]; then source .venv/bin/activate; fi
PYTHONPATH="src" python -m dzac destroy --manifest "$MANIFEST" --profile "$PROFILE" --region "$REGION"
