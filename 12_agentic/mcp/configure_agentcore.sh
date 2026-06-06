# Step 3: Configure AgentCore deployment (NO PROJECT FILES NEEDED!)
echo "⚙️  Step 3: Configuring AgentCore deployment..."
agentcore configure \
  --container-image public.ecr.aws/awslabs-mcp/awslabs/redshift-mcp-server:latest \
  --protocol MCP \
  --execution-role $ROLE_ARN \
  --env AWS_REDSHIFT_MCP_TRANSPORT=streamable-http \
  --env AWS_REDSHIFT_MCP_HOST=0.0.0.0 \
  --env AWS_REDSHIFT_MCP_PORT=8000 \
  --env AWS_DEFAULT_REGION=us-east-1 \
  --env FASTMCP_LOG_LEVEL=ERROR

# During interactive configuration, provide:
echo "📝 During configuration prompts, provide:"
echo "   - Discovery URL: https://cognito-idp.us-east-1.amazonaws.com/$POOL_ID/.well-known/openid-configuration"
echo "   - Client ID: $CLIENT_ID"

