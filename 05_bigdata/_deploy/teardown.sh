#!/usr/bin/env bash
# Orchestrator: tear down all four projects + shared infra.
#
# Order matters — projects own per-resource artefacts that depend on the
# shared bucket and IAM roles, so projects come first.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-dq-demo}"

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

echo "=============================================================="
echo "Teardown all dq-demo resources (region=$REGION prefix=$PREFIX)"
echo "Account: $(aws sts get-caller-identity --query Account --output text)"
echo "=============================================================="
echo

# Project teardowns first (any order; they only touch their own resources).
"$ROOT/lambda-api-ingestion/teardown.sh"
"$ROOT/glue-databrew-profiling/teardown.sh"
"$ROOT/glue-deequ-data-quality/teardown.sh"
"$ROOT/kiro-data-pipeline/teardown.sh"

# Shared infra last (IAM roles + S3 bucket).
"$HERE/teardown-shared.sh"

echo
echo "All teardown scripts completed."
