#!/bin/bash
# Tear down the Stock Trading Demo: CDK stack + S3 Tables (table, namespace,
# bucket). The S3 Tables resources are out-of-band because they aren't yet
# first-class CFN resources, so we delete them here.

set -euo pipefail

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)
S3T_BUCKET_ARN="arn:aws:s3tables:${REGION}:${ACCOUNT_ID}:bucket/stock-trading-demo"

echo "🔥 Destroying CDK stack..."
(cd cdk && cdk destroy --force StockTradingStack || true)

echo "🗑️  Deleting S3 Tables 'trades' table..."
aws s3tables delete-table \
    --table-bucket-arn "$S3T_BUCKET_ARN" \
    --namespace trading \
    --name trades 2>/dev/null || echo "   (table already gone)"

echo "🗑️  Deleting S3 Tables 'trading' namespace..."
aws s3tables delete-namespace \
    --table-bucket-arn "$S3T_BUCKET_ARN" \
    --namespace trading 2>/dev/null || echo "   (namespace already gone)"

read -r -p "Delete the S3 Tables bucket 'stock-trading-demo' itself? [y/N] " confirm
if [[ "$confirm" =~ ^[Yy]$ ]]; then
    aws s3tables delete-table-bucket --table-bucket-arn "$S3T_BUCKET_ARN"
    echo "✅ Bucket deleted."
else
    echo "ℹ️  Bucket retained: $S3T_BUCKET_ARN"
fi

echo "✅ Teardown complete."
