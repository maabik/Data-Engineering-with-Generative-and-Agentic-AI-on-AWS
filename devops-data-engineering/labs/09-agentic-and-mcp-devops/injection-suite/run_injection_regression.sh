#!/usr/bin/env bash
# Run the prompt-injection harness against the deployed agent and fail
# if the injection success rate regressed vs the previous baseline.
#
# Wires to ../../02_security/prompt-injection-test-harness/.
set -euo pipefail

AGENT_ARN="${AGENT_ARN:?set AGENT_ARN to the deployed agent runtime ARN}"
BASELINE="${BASELINE:-injection-suite/baseline.json}"
HARNESS_DIR="${HARNESS_DIR:-../../02_security/prompt-injection-test-harness}"

cd "$(dirname "$0")"

# Run the harness — produces report.json with {success_rate, blocked_rate}.
python "$HARNESS_DIR/run.py" --target agentcore --arn "$AGENT_ARN" --report report.json

if [[ -f "$BASELINE" ]]; then
  prev=$(jq -r .success_rate "$BASELINE")
  curr=$(jq -r .success_rate report.json)
  echo "Injection success rate: $curr (baseline $prev)"
  python -c "import sys; sys.exit(0 if float('$curr') <= float('$prev') + 0.01 else 1)" \
    || { echo "Injection success rate regressed."; exit 1; }
else
  echo "No baseline yet; recording current as new baseline."
  cp report.json "$BASELINE"
fi
