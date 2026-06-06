#!/bin/bash

set -e

echo "🚀 Creating Sales Agent Execution Role for AgentCore..."

# Look up the caller's account ID at runtime so this script never carries
# a hardcoded account ID. The AgentCoreRuntime{ECR,JWT}Policy policies must
# already exist in the same account (created by AgentCore CLI on first
# `agentcore launch`).
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# Create trust policy for AgentCore
TRUST_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "bedrock-agentcore.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
)

# Create the execution role
ROLE_ARN=$(aws iam create-role \
  --role-name "SalesAgentExecutionRole" \
  --assume-role-policy-document "$TRUST_POLICY" \
  --description "Execution role for Sales Agent on AgentCore runtime" \
  --region us-east-1 | jq -r '.Role.Arn')

echo "✅ Created role: $ROLE_ARN"

# Attach existing AgentCore policies
aws iam attach-role-policy \
  --role-name "SalesAgentExecutionRole" \
  --policy-arn "arn:aws:iam::${ACCOUNT_ID}:policy/AgentCoreRuntimeECRPolicy" \
  --region us-east-1

echo "✅ Attached AgentCoreRuntimeECRPolicy"

aws iam attach-role-policy \
  --role-name "SalesAgentExecutionRole" \
  --policy-arn "arn:aws:iam::${ACCOUNT_ID}:policy/AgentCoreRuntimeJWTPolicy" \
  --region us-east-1

echo "✅ Attached AgentCoreRuntimeJWTPolicy"

# Add Bedrock and Secrets Manager access
ADDITIONAL_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel"
      ],
      "Resource": [
        "arn:aws:bedrock:us-east-1::foundation-model/anthropic.claude-sonnet-4-6",
        "arn:aws:bedrock:us-east-1:*:inference-profile/us.anthropic.claude-sonnet-4-6"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": "arn:aws:secretsmanager:us-east-1:*:secret:sales-agent-credentials*"
    }
  ]
}
EOF
)

aws iam put-role-policy \
  --role-name "SalesAgentExecutionRole" \
  --policy-name "SalesAgentAdditionalAccess" \
  --policy-document "$ADDITIONAL_POLICY" \
  --region us-east-1

echo "✅ Added Bedrock and Secrets Manager access"

echo ""
echo "🎯 Sales Agent Execution Role Created:"
echo "Role ARN: $ROLE_ARN"
echo ""
echo "✅ Role ready for AgentCore deployment!"
