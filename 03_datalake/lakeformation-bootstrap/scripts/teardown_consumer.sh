#!/bin/bash
# Tear down consumer-side artifacts created when testing the cross-account
# share.
#
# Run in the CONSUMER account, in the same region as the producer deploy.
#
# Removes (in order):
#   1. The consumer analyst IAM role (lf-consumer-analyst) and its policies
#   2. The Athena query-results bucket
#   3. The Glue resource link (chicago_violations_link)
#
# Notes:
#   - Does NOT disassociate RAM shares — Glue catalog/database/table resource
#     types do not support consumer-side leave. The producer's `cdk destroy`
#     revokes the LF grants, which auto-deletes the underlying RAM shares.
#   - Does NOT remove the consumer's LF admin status — that may be in use by
#     other workloads in the account.
#   - Does NOT remove the consumer's IAM permissions on the principal who
#     ran the test (the IAM user/role you authenticated as).

set -e

PROFILE="${AWS_PROFILE:-default-consumer}"
REGION="${AWS_REGION:-us-east-1}"

ACCT=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)
RESULTS_BUCKET="lf-consumer-athena-results-${ACCT}-${REGION}"
ROLE_NAME=lf-consumer-analyst
LINK_DB=chicago_violations_link

echo "=== Tearing down consumer-side artifacts ==="
echo "  Profile: $PROFILE"
echo "  Account: $ACCT"
echo "  Region:  $REGION"
echo

# 1. Delete the consumer analyst role
if aws iam get-role --profile "$PROFILE" --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "[1/3] Removing IAM role: $ROLE_NAME"
  for arn in $(aws iam list-attached-role-policies --profile "$PROFILE" --role-name "$ROLE_NAME" \
                 --query 'AttachedPolicies[].PolicyArn' --output text); do
    aws iam detach-role-policy --profile "$PROFILE" --role-name "$ROLE_NAME" --policy-arn "$arn"
  done
  for pol in $(aws iam list-role-policies --profile "$PROFILE" --role-name "$ROLE_NAME" \
                 --query 'PolicyNames' --output text); do
    aws iam delete-role-policy --profile "$PROFILE" --role-name "$ROLE_NAME" --policy-name "$pol"
  done
  aws iam delete-role --profile "$PROFILE" --role-name "$ROLE_NAME"
else
  echo "[1/3] Role $ROLE_NAME not present, skipping."
fi

# 2. Remove the Athena results bucket
if aws s3api head-bucket --profile "$PROFILE" --bucket "$RESULTS_BUCKET" 2>/dev/null; then
  echo "[2/3] Removing Athena results bucket: $RESULTS_BUCKET"
  aws s3 rb --profile "$PROFILE" "s3://$RESULTS_BUCKET" --force
else
  echo "[2/3] Athena results bucket not present, skipping."
fi

# 3. Drop the Glue resource link
if aws glue get-database --profile "$PROFILE" --region "$REGION" --name "$LINK_DB" >/dev/null 2>&1; then
  echo "[3/3] Removing Glue resource link: $LINK_DB"
  aws glue delete-database --profile "$PROFILE" --region "$REGION" --name "$LINK_DB"
else
  echo "[3/3] Resource link $LINK_DB not present, skipping."
fi

echo
echo "=== Consumer teardown complete ==="
echo
echo "RAM shares from the producer remain associated with this account; they"
echo "will be deleted automatically when the producer runs teardown_producer.sh"
echo "(which revokes the LF grants that own the shares)."
