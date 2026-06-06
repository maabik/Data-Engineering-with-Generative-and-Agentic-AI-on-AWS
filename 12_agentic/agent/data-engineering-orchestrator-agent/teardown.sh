#!/usr/bin/env bash
# Idempotent teardown for data-engineering-orchestrator-agent.
# Deletes (in order): runtime, ECR repo, IAM role, SSM param.
# Does NOT touch any downstream specialist agent.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-data-engineering-orchestrator-agent}"

ECR_REPO="${PREFIX}"
RUNTIME_NAME="data_engineering_orchestrator_agent"
RT_ROLE="${PREFIX}-runtime-role"

echo "[orchestrator] teardown region=$REGION"

# 1. AgentCore Runtime
RT_ID="$(aws bedrock-agentcore-control list-agent-runtimes --no-paginate --region "$REGION" \
  --query "agentRuntimes[?agentRuntimeName=='${RUNTIME_NAME}'].agentRuntimeId | [0]" \
  --output text 2>/dev/null || true)"
if [ -n "$RT_ID" ] && [ "$RT_ID" != "None" ]; then
  echo "  delete runtime $RUNTIME_NAME ($RT_ID)"
  aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id "$RT_ID" --region "$REGION" >/dev/null || true
fi

# 2. ECR
aws ecr delete-repository --repository-name "$ECR_REPO" --force --region "$REGION" >/dev/null 2>&1 || true

# 3. IAM role
if aws iam get-role --role-name "$RT_ROLE" >/dev/null 2>&1; then
  for p in $(aws iam list-role-policies --role-name "$RT_ROLE" --query 'PolicyNames[]' --output text 2>/dev/null || true); do
    aws iam delete-role-policy --role-name "$RT_ROLE" --policy-name "$p" >/dev/null || true
  done
  aws iam delete-role --role-name "$RT_ROLE" >/dev/null || true
  echo "  deleted role $RT_ROLE"
fi

# 4. SSM
aws ssm delete-parameters --names "/${PREFIX}/agent_runtime_arn" --region "$REGION" >/dev/null 2>&1 || true

echo "✅ teardown complete"
