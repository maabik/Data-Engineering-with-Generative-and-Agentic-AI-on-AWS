#!/usr/bin/env bash
# Tear down everything this project deploys to AWS.
#
# Order matters:
#   1. Delete the AgentCore Runtimes + CodeBuild + ECR + Cognito +
#      S3 Vectors index/bucket + Secrets Manager + SSM. The AgentCore
#      starter toolkit creates these *outside* CDK; cdk destroy doesn't
#      touch them, so we use python -m knowledge_graph_agent.teardown.
#   2. cdk destroy KGMcp KGObservability KGIngestion KGNeptune
#      The Neptune Analytics graph, S3 buckets (auto-delete enabled),
#      Lambda image, IAM, KMS, Step Functions, CloudWatch dashboard
#      all live in those stacks.
#
# Idempotent: re-running is safe — every step swallows "not found".
#
# Usage:
#   bash scripts/teardown.sh              # interactive confirmation
#   bash scripts/teardown.sh --yes        # non-interactive
#   bash scripts/teardown.sh --keep-iam   # leave AgentCore IAM roles intact

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

REGION="${AWS_REGION:-us-east-1}"
SKIP_PROMPT=false
KEEP_IAM_ARG=""
for arg in "$@"; do
  case "$arg" in
    --yes|-y) SKIP_PROMPT=true ;;
    --keep-iam) KEEP_IAM_ARG="--keep-iam" ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

echo "🧨 KG agent teardown — region: ${REGION}"
echo "   This will delete the Neptune Analytics graph, both AgentCore"
echo "   runtimes, ECR repos, Cognito pool, S3 Vectors bucket/index,"
echo "   the documents bucket (auto-delete), and the four CDK stacks."
echo

if ! $SKIP_PROMPT; then
  read -rp "Continue? [y/N] " ok
  if [[ "${ok:-N}" != "y" && "${ok:-N}" != "Y" ]]; then
    echo "aborted"
    exit 1
  fi
fi

echo
echo "── Step 1/2: toolkit-managed resources ──────────────────────────"
AWS_REGION="$REGION" PYTHONPATH=src python3 -m knowledge_graph_agent.teardown --region "$REGION" $KEEP_IAM_ARG

echo
echo "── Step 2/2: CDK stacks ─────────────────────────────────────────"
pushd infrastructure/cdk >/dev/null
AWS_REGION="$REGION" JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 \
  cdk destroy --force KGMcp KGObservability KGIngestion KGNeptune
popd >/dev/null

echo
echo "✅ Teardown complete."
echo
echo "Verify nothing lingers:"
echo "  aws neptune-graph list-graphs --region ${REGION}"
echo "  aws s3vectors list-vector-buckets --region ${REGION}"
echo "  aws bedrock-agentcore-control list-agent-runtimes --region ${REGION}"
echo "  aws cloudformation list-stacks --region ${REGION} \\"
echo "      --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE \\"
echo "      --query 'StackSummaries[?starts_with(StackName,\`KG\`)].StackName'"
