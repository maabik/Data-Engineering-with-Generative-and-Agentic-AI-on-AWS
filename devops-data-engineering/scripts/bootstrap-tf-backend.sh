#!/usr/bin/env bash
# One-time bootstrap for the Terraform S3 backend used by the reference
# pipeline. Creates the state bucket and DynamoDB lock table.
#
# Idempotent. Safe to re-run.
#
# Usage:
#   ./bootstrap-tf-backend.sh <state-bucket-name> <lock-table-name> [region]
set -euo pipefail

bucket="${1:?state bucket name}"
table="${2:?lock table name}"
region="${3:-us-east-1}"

account=$(aws sts get-caller-identity --query Account --output text)
echo "Bootstrapping Terraform backend in account ${account}, region ${region}"

if aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
  echo "  bucket s3://${bucket} exists"
else
  if [[ "$region" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$bucket" --region "$region"
  else
    aws s3api create-bucket --bucket "$bucket" --region "$region" \
      --create-bucket-configuration LocationConstraint="$region"
  fi
  aws s3api put-bucket-versioning --bucket "$bucket" --versioning-configuration Status=Enabled
  aws s3api put-public-access-block --bucket "$bucket" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  aws s3api put-bucket-encryption --bucket "$bucket" --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  echo "  created s3://${bucket} with versioning + SSE + public-access-block"
fi

if aws dynamodb describe-table --region "$region" --table-name "$table" >/dev/null 2>&1; then
  echo "  lock table ${table} exists"
else
  aws dynamodb create-table --region "$region" \
    --table-name "$table" \
    --attribute-definitions AttributeName=LockID,AttributeType=S \
    --key-schema AttributeName=LockID,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST
  aws dynamodb wait table-exists --region "$region" --table-name "$table"
  echo "  created lock table ${table}"
fi

echo
echo "Done. Update reference-pipeline/terraform/envs/*/backend.tf with:"
echo "  bucket         = \"${bucket}\""
echo "  dynamodb_table = \"${table}\""
echo "  region         = \"${region}\""
