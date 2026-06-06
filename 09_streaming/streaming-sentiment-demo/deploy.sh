#!/bin/bash
# One-command deploy for the streaming sentiment demo.
set -euo pipefail

for cmd in aws npx node; do
    if ! command -v "$cmd" &> /dev/null; then
        echo "❌ '$cmd' not on PATH"; exit 1
    fi
done
aws sts get-caller-identity > /dev/null || { echo "❌ AWS creds not configured"; exit 1; }

export CDK_DEFAULT_ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
export CDK_DEFAULT_REGION="$(aws configure get region)"
: "${CDK_DEFAULT_REGION:?AWS region not configured}"

echo "📦 Installing CDK deps..."
npm install --silent

echo "🏗️  CDK bootstrap (no-op if already done)..."
npx cdk bootstrap "aws://${CDK_DEFAULT_ACCOUNT}/${CDK_DEFAULT_REGION}" >/dev/null

echo "🚀 Deploying StreamingSentimentDemoPythonStack..."
npx cdk deploy --require-approval never --outputs-file outputs.json

echo "✅ Done. Outputs in outputs.json"
