#!/usr/bin/env bash
set -euo pipefail

STACK_NAME="${STACK_NAME:-mcp-oauth-reference}"
REGION="${AWS_REGION:-$(aws configure get region)}"

aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION"
echo "Deleted $STACK_NAME"
