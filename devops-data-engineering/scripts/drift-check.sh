#!/usr/bin/env bash
# Local drift check. Runs terraform plan with -detailed-exitcode and
# prints a human-readable summary. Exit code 0 = no drift, 2 = drift.
# Use to spot-check before opening a PR.
#
# Usage: ./drift-check.sh [sandbox|prod]
set -euo pipefail

env="${1:-sandbox}"
cd "$(dirname "$0")/../reference-pipeline/terraform/envs/${env}"

terraform init -input=false >/dev/null

set +e
terraform plan -detailed-exitcode -no-color -lock=false -out drift.tfplan
status=$?
set -e

case "$status" in
  0) echo "No drift in ${env}." ;;
  2) echo "DRIFT in ${env}. Plan written to drift.tfplan; run 'terraform show drift.tfplan' for detail." ;;
  *) echo "terraform plan failed (exit ${status})."; exit "$status" ;;
esac

exit "$status"
