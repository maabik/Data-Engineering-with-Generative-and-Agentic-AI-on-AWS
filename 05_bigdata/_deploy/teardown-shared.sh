#!/usr/bin/env bash
# Teardown for shared infra used by all four projects.
#
# Deletes (in safe order):
#   - IAM roles    dq-demo-lambda-role, dq-demo-glue-role, dq-demo-databrew-role
#                  (detaches managed + inline policies first)
#   - S3 bucket    dq-demo-${ACCOUNT}-${AWS_REGION}    (empties first)
#
# Run this LAST, after every project teardown.sh has succeeded.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-dq-demo}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${BUCKET:-${PREFIX}-${ACCOUNT}-${REGION}}"

ROLES=("${PREFIX}-lambda-role" "${PREFIX}-glue-role" "${PREFIX}-databrew-role")

echo "[shared] tearing down (region=$REGION account=$ACCOUNT bucket=$BUCKET)"

# 1. IAM roles
for ROLE in "${ROLES[@]}"; do
    if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
        echo "  detach managed policies on $ROLE"
        for arn in $(aws iam list-attached-role-policies --role-name "$ROLE" \
                      --query 'AttachedPolicies[].PolicyArn' --output text); do
            [ -n "$arn" ] && [ "$arn" != "None" ] || continue
            aws iam detach-role-policy --role-name "$ROLE" --policy-arn "$arn"
        done
        echo "  delete inline policies on $ROLE"
        for pol in $(aws iam list-role-policies --role-name "$ROLE" \
                      --query 'PolicyNames[]' --output text); do
            [ -n "$pol" ] && [ "$pol" != "None" ] || continue
            aws iam delete-role-policy --role-name "$ROLE" --policy-name "$pol"
        done
        echo "  delete role $ROLE"
        aws iam delete-role --role-name "$ROLE"
    fi
done

# 2. S3 bucket — empty (including all object versions if versioned), then delete
if aws s3api head-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null 2>&1; then
    echo "  empty s3://$BUCKET (objects + versions)"
    aws s3 rm "s3://$BUCKET" --recursive >/dev/null || true
    # Delete versions/delete-markers (if versioning was ever enabled)
    aws s3api list-object-versions --bucket "$BUCKET" \
        --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null \
      | python3 -c '
import json, sys, subprocess
data = json.load(sys.stdin)
objs = (data or {}).get("Objects") or []
if objs:
    for i in range(0, len(objs), 1000):
        batch = {"Objects": objs[i:i+1000], "Quiet": True}
        subprocess.run(["aws","s3api","delete-objects","--bucket","'"$BUCKET"'","--delete",json.dumps(batch)], check=False)
'
    aws s3api list-object-versions --bucket "$BUCKET" \
        --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}' --output json 2>/dev/null \
      | python3 -c '
import json, sys, subprocess
data = json.load(sys.stdin)
objs = (data or {}).get("Objects") or []
if objs:
    for i in range(0, len(objs), 1000):
        batch = {"Objects": objs[i:i+1000], "Quiet": True}
        subprocess.run(["aws","s3api","delete-objects","--bucket","'"$BUCKET"'","--delete",json.dumps(batch)], check=False)
'
    echo "  delete bucket $BUCKET"
    aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION"
fi

echo "[shared] done"
