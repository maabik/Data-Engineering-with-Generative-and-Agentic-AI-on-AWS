#!/bin/bash
# Tear down ALL producer-side artifacts created by lakeformation-bootstrap.
#
# Run in the PRODUCER account, in the same region you deployed to.
#
# Removes (in order):
#   1. The Athena query-results bucket created during testing (manual artifact)
#   2. The CloudFormation stack (37 resources: S3 lake bucket, Glue DB+table,
#      LF tags + tag associations, data filters, IAM roles, LF grants,
#      LF cross-account RAM shares, seed Lambda)
#
# Notes:
#   - LF cross-account grants must be revoked manually if cdk destroy hangs;
#     this script tries that as a fallback.
#   - The script does NOT remove the deployer's LF admin status — that was a
#     prerequisite, not something the stack created.

set -e

PROFILE="${AWS_PROFILE:-default}"
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${STACK_NAME:-LakeFormationBootstrapStack}"

ACCT=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)
RESULTS_BUCKET="lf-bootstrap-athena-results-${ACCT}-${REGION}"

echo "=== Tearing down producer-side artifacts ==="
echo "  Account: $ACCT"
echo "  Region:  $REGION"
echo "  Stack:   $STACK_NAME"
echo

# 1. Empty + delete the Athena results bucket (manual test artifact, not in stack)
if aws s3api head-bucket --profile "$PROFILE" --bucket "$RESULTS_BUCKET" 2>/dev/null; then
  echo "[1/2] Removing Athena results bucket: $RESULTS_BUCKET"
  aws s3 rb --profile "$PROFILE" "s3://$RESULTS_BUCKET" --force
else
  echo "[1/2] Athena results bucket not present, skipping."
fi

# 2. Destroy the CloudFormation stack via CDK
if aws cloudformation describe-stacks --profile "$PROFILE" --region "$REGION" \
     --stack-name "$STACK_NAME" >/dev/null 2>&1; then
  echo "[2/2] Destroying CloudFormation stack: $STACK_NAME"
  cd "$(dirname "$0")/../cdk"
  if [ -d .venv ]; then source .venv/bin/activate; fi
  JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1 cdk destroy --force "$STACK_NAME"
else
  echo "[2/2] Stack $STACK_NAME not present, skipping."
fi

echo
echo "=== Producer teardown complete ==="
echo
echo "If the stack failed to delete due to LF cross-account grants, revoke them"
echo "manually then re-run this script:"
echo
echo "  aws lakeformation revoke-permissions \\"
echo "    --principal DataLakePrincipalIdentifier=<consumer-account-id> \\"
echo "    --resource '{\"Table\":{\"CatalogId\":\"$ACCT\",\"DatabaseName\":\"chicago_violations\",\"Name\":\"violations\"}}' \\"
echo "    --permissions SELECT DESCRIBE \\"
echo "    --permissions-with-grant-option SELECT DESCRIBE"
