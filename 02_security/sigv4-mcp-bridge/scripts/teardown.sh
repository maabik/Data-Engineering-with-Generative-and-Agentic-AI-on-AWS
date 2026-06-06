#!/usr/bin/env bash
set -euo pipefail

STACK_NAME="${STACK_NAME:-sigv4-mcp-bridge}"
REGION="${AWS_REGION:-$(aws configure get region)}"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${LAMBDA_BUCKET:-sigv4-mcp-bridge-lambda-${ACCOUNT_ID}-${REGION}}"

aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION"
echo "Deleted $STACK_NAME"

if [ "${DELETE_BUCKET:-}" = "1" ]; then
  # Purge all object versions and delete markers before deleting the bucket;
  # versioned buckets can't be deleted otherwise.
  python3 - <<PY
import boto3
s3 = boto3.client("s3", region_name="$REGION")
paginator = s3.get_paginator("list_object_versions")
objs = []
for page in paginator.paginate(Bucket="$BUCKET"):
    for v in (page.get("Versions") or []) + (page.get("DeleteMarkers") or []):
        objs.append({"Key": v["Key"], "VersionId": v["VersionId"]})
        if len(objs) == 1000:
            s3.delete_objects(Bucket="$BUCKET", Delete={"Objects": objs})
            objs = []
if objs:
    s3.delete_objects(Bucket="$BUCKET", Delete={"Objects": objs})
PY
  aws s3api delete-bucket --bucket "$BUCKET" --region "$REGION" || true
  echo "Deleted staging bucket $BUCKET"
else
  echo "Staging bucket $BUCKET retained. Set DELETE_BUCKET=1 to remove it."
fi
