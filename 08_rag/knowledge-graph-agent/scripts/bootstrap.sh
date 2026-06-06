#!/usr/bin/env bash
# One-command setup for the knowledge-graph-agent project.
#
# Idempotent. Re-running is safe — it just confirms each piece is in place.
#
# Steps:
#   1. install Python deps
#   2. cdk synth (does not deploy automatically — review first)
#   3. print the next-action commands the developer should run

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "📦 [1/3] Installing Python dependencies..."
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -e ".[mcp,agent,cdk,dev]"

echo
echo "🏗️  [2/3] Synthesizing CDK stacks..."
pushd infrastructure/cdk >/dev/null
if ! command -v cdk >/dev/null 2>&1; then
  echo "   (cdk CLI not on PATH; skipping synth — install with: npm install -g aws-cdk)"
else
  cdk synth >/dev/null
  echo "   ✅ cdk synth clean"
fi
popd >/dev/null

echo
echo "✅ [3/3] Bootstrap complete. Next steps:"
cat <<'EOF'

   Deploy the foundations (idempotent; re-runnable):
     cd infrastructure/cdk
     cdk deploy KGNeptune KGIngestion KGObservability KGMcp

   After deploy, capture configuration:
     export KG_GRAPH_ID=$(aws ssm get-parameter --name /kg/graph_id --query Parameter.Value --output text)
     export KG_S3_VECTORS_BUCKET=$(aws ssm get-parameter --name /kg/vectors_bucket --query Parameter.Value --output text)
     export KG_REGION=${AWS_REGION:-us-east-1}

   Deploy the MCP server to AgentCore Runtime:
     KG_COGNITO_PASSWORD=ChangeMe1! python -m knowledge_graph_agent.mcp_server.deploy

   Run the flagship example:
     python examples/pe_due_diligence/ingest_dataroom.py
     python examples/pe_due_diligence/run_agent.py --all-samples

EOF
