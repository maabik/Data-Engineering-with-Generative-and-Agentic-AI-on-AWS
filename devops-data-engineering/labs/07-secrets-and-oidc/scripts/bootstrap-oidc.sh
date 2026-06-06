#!/usr/bin/env bash
# Bootstrap GitHub OIDC provider + per-environment deploy role.
# Run once per AWS account.
#
# Usage:
#   ./bootstrap-oidc.sh sandbox  your-org/your-repo  '*'
#   ./bootstrap-oidc.sh staging  your-org/your-repo  'refs/heads/main'
#   ./bootstrap-oidc.sh prod     your-org/your-repo  'refs/tags/v*'
set -euo pipefail

env="${1:?environment name (sandbox|staging|prod)}"
repo="${2:?github repo (org/repo)}"
ref="${3:-*}"

cd "$(dirname "$0")/.."

terraform init

# OIDC provider is shared across environments in the same account.
terraform apply -auto-approve -target=aws_iam_openid_connect_provider.github

terraform apply -auto-approve \
  -var "environment_name=${env}" \
  -var "github_repo=${repo}" \
  -var "ref_pattern=${ref}"

echo
echo "Deploy role ARN:"
terraform output -raw deploy_role_arn
echo
echo "Add this to GitHub repo settings → Environments → ${env} → Secrets:"
echo "  ${env^^}_DEPLOY_ROLE = <the ARN above>"
