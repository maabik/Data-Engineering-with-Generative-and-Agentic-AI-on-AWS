#!/usr/bin/env bash
# Block hardcoded 12-digit AWS account IDs in IaC files.
#
# Why this hook exists: tfsec / checkov don't flag a literal account ID in
# a resource's ARN string. Account IDs in IaC are usually a sign that
# someone hardcoded a sandbox / production account where the value should
# come from a variable or data source. Real account IDs in a public repo
# are also reconnaissance gold for attackers.
#
# Allowlist: 000000000000 (LocalStack), 123456789012 (AWS docs example).
set -euo pipefail

PATTERN='\b[0-9]{12}\b'
ALLOWLIST='^(000000000000|123456789012)$'

failed=0
for f in "$@"; do
  while IFS= read -r match; do
    [[ -z "$match" ]] && continue
    if [[ "$match" =~ $ALLOWLIST ]]; then continue; fi
    echo "ERROR: hardcoded AWS account ID in $f: $match"
    echo "       use a variable, data.aws_caller_identity, or a tfvars value."
    failed=1
  done < <(grep -hoE "$PATTERN" "$f" || true)
done

exit "$failed"
