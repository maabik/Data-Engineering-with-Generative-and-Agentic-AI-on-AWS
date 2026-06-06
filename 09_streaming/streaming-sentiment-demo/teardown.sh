#!/bin/bash
# Tear down the streaming sentiment demo.
set -euo pipefail

echo "🔥 Destroying StreamingSentimentDemoPythonStack..."
npx cdk destroy --force StreamingSentimentDemoPythonStack || true

# Dashboard is provisioned by CDK, so no separate cleanup needed.
echo "✅ Teardown complete."
