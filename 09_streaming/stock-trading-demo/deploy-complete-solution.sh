#!/bin/bash
# Stock Trading Demo - one-command deploy.
#
# Everything (Kinesis, Firehose, Glue, Lake Formation grants, IAM, Lambda,
# CloudWatch dashboard + alarms) is provisioned by CDK. This script just sets
# up the Python venv, verifies the S3 Tables bucket exists (S3 Tables buckets
# are not yet first-class CFN resources), and runs `cdk deploy`.

set -euo pipefail

echo "🚀 Deploying Stock Trading Demo"
echo "================================"

for cmd in aws cdk python3; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "❌ '$cmd' not found on PATH"
        exit 1
    fi
done

aws sts get-caller-identity > /dev/null || { echo "❌ AWS credentials not configured"; exit 1; }

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)
: "${REGION:?AWS region not configured (run 'aws configure')}"

export CDK_DEFAULT_ACCOUNT="$ACCOUNT_ID"
export CDK_DEFAULT_REGION="$REGION"

echo "✅ Account: $ACCOUNT_ID  Region: $REGION"

echo "🗂️  Verifying S3 Tables bucket 'stock-trading-demo'..."
if ! aws s3tables list-table-buckets --query 'tableBuckets[?name==`stock-trading-demo`]' --output text | grep -q "stock-trading-demo"; then
    echo "❌ S3 Tables bucket 'stock-trading-demo' not found."
    echo "   Create it once via the console (S3 → Table buckets) or:"
    echo "   aws s3tables create-table-bucket --name stock-trading-demo --region $REGION"
    exit 1
fi

echo "🗂️  Verifying S3 Tables namespace 'trading'..."
S3T_BUCKET_ARN="arn:aws:s3tables:${REGION}:${ACCOUNT_ID}:bucket/stock-trading-demo"
if ! aws s3tables list-namespaces --table-bucket-arn "$S3T_BUCKET_ARN" --query 'namespaces[?namespace[0]==`trading`]' --output text | grep -q "trading"; then
    echo "📝 Creating namespace 'trading'..."
    aws s3tables create-namespace --table-bucket-arn "$S3T_BUCKET_ARN" --namespace trading
fi

echo "🗂️  Verifying S3 Tables table 'trades'..."
if ! aws s3tables get-table --table-bucket-arn "$S3T_BUCKET_ARN" --namespace trading --name trades > /dev/null 2>&1; then
    echo "📝 Creating table 'trades' (Iceberg)..."
    aws s3tables create-table \
        --table-bucket-arn "$S3T_BUCKET_ARN" \
        --namespace trading \
        --name trades \
        --format ICEBERG
fi

echo "🐍 Setting up Python environment..."
if [ ! -d venv ]; then
    python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install --quiet -r cdk/requirements.txt
pip install --quiet -r scripts/requirements.txt

echo "🏗️  CDK bootstrap (no-op if already done)..."
(cd cdk && cdk bootstrap "aws://${ACCOUNT_ID}/${REGION}" >/dev/null)

echo "🚀 Deploying CDK stack..."
(cd cdk && cdk deploy --require-approval never)

S3_BUCKET=$(aws cloudformation describe-stacks --stack-name StockTradingStack \
    --query 'Stacks[0].Outputs[?OutputKey==`S3BucketName`].OutputValue' --output text)
SNS_TOPIC=$(aws cloudformation describe-stacks --stack-name StockTradingStack \
    --query 'Stacks[0].Outputs[?OutputKey==`SNSTopicARN`].OutputValue' --output text)

cat <<EOF

🎉 Deployment complete!

S3 backup bucket: $S3_BUCKET
SNS topic:        $SNS_TOPIC

Next steps:
  1. Subscribe to SNS:
     aws sns subscribe --topic-arn "$SNS_TOPIC" --protocol email --notification-endpoint you@example.com
  2. Generate trades:
     python3 scripts/trade_generator.py
  3. Watch Firehose deliveries:
     aws logs tail /aws/kinesisfirehose/KDS-s3tables-trades --follow
  4. Query Iceberg in Athena:
     SELECT * FROM trading.trades LIMIT 10;
  5. Tear down:
     ./teardown.sh
EOF
