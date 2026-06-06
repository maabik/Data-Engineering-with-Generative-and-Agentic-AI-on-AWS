#!/bin/bash

# Create Cognito Resource Server for AgentCore Audience
# This script creates the resource server needed for allowedAudience configuration

USER_POOL_ID="us-east-1_vCe0yuRgY"
RESOURCE_SERVER_IDENTIFIER="gateway-resource-server"
RESOURCE_SERVER_NAME="AgentCore Gateway Resource Server"

echo "🔧 Creating Cognito Resource Server..."

# Create the resource server
aws cognito-idp create-resource-server \
    --user-pool-id $USER_POOL_ID \
    --identifier $RESOURCE_SERVER_IDENTIFIER \
    --name "$RESOURCE_SERVER_NAME" \
    --scopes ScopeName=read,ScopeDescription="Read access to gateway resources" \
    --region us-east-1

if [ $? -eq 0 ]; then
    echo "✅ Resource server created successfully!"
    echo "📋 Resource Server Details:"
    echo "   - Identifier: $RESOURCE_SERVER_IDENTIFIER"
    echo "   - User Pool: $USER_POOL_ID"
    echo "   - Scope: $RESOURCE_SERVER_IDENTIFIER/read"
    echo ""
    echo "🔑 To get tokens with this audience, use scope: $RESOURCE_SERVER_IDENTIFIER/read"
else
    echo "❌ Failed to create resource server"
    exit 1
fi
