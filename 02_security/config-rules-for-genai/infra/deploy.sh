#!/usr/bin/env bash
# Deploy all GenAI Config rules to AWS.
# Usage: ./infra/deploy.sh [--region us-east-1] [--prefix genai] [--bucket my-staging-bucket]

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || echo us-east-1)}"
STACK="genai-config-rules"
PREFIX="genai"
BUCKET=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --region)   REGION="$2"; shift 2 ;;
    --prefix)   PREFIX="$2"; shift 2 ;;
    --bucket)   BUCKET="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "Account: ${ACCOUNT} | Region: ${REGION} | Prefix: ${PREFIX}"

# Create staging bucket if not provided
if [[ -z "$BUCKET" ]]; then
  BUCKET="${PREFIX}-config-rules-${ACCOUNT}-${REGION}"
  aws s3api head-bucket --bucket "${BUCKET}" --region "${REGION}" 2>/dev/null \
    || aws s3 mb "s3://${BUCKET}" --region "${REGION}"
  echo "Staging bucket: s3://${BUCKET}"
fi

# Package rules into a zip
ZIP="${ROOT}/build/rules.zip"
mkdir -p "${ROOT}/build"
cd "${ROOT}"
zip -r "${ZIP}" lib/ rules/ -x "**/__pycache__/*" -x "**/*.pyc" -q
echo "Packaged rules → ${ZIP}"

# Upload
S3_KEY="${PREFIX}/rules.zip"
aws s3 cp "${ZIP}" "s3://${BUCKET}/${S3_KEY}" --region "${REGION}"
echo "Uploaded → s3://${BUCKET}/${S3_KEY}"

# Deploy stack
aws cloudformation deploy \
  --template-file "${SCRIPT_DIR}/config_rules.yaml" \
  --stack-name "${STACK}" \
  --region "${REGION}" \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides \
    LambdaCodeBucket="${BUCKET}" \
    LambdaCodeKey="${S3_KEY}" \
    ConfigRulePrefix="${PREFIX}" \
  --no-fail-on-empty-changeset

echo ""
echo "Stack '${STACK}' deployed successfully in ${REGION}."
echo "Rules active:"
aws config describe-config-rules --query 'ConfigRules[?starts_with(ConfigRuleName,`'"${PREFIX}"'`)].{Name:ConfigRuleName,State:ConfigRuleState}' \
  --output table --region "${REGION}" 2>/dev/null || true
