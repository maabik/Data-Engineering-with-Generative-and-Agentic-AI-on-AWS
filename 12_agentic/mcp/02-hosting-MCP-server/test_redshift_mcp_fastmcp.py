#!/usr/bin/env python3
"""Test the deployed Redshift MCP server remotely using FastMCP client."""

import asyncio
import boto3
import json
import sys
from boto3.session import Session
from fastmcp import Client

async def main():
    """Test the deployed Redshift MCP server."""
    boto_session = Session()
    region = boto_session.region_name
    
    print(f"Using AWS region: {region}")
    
    try:
        # Retrieve Agent ARN from Parameter Store
        ssm_client = boto3.client('ssm', region_name=region)
        agent_arn_response = ssm_client.get_parameter(Name='/redshift_mcp_server/runtime/agent_arn')
        agent_arn = agent_arn_response['Parameter']['Value']
        print(f"Retrieved Agent ARN: {agent_arn}")

        # Retrieve Cognito credentials from Secrets Manager
        secrets_client = boto3.client('secretsmanager', region_name=region)
        response = secrets_client.get_secret_value(SecretId='redshift_mcp_server/cognito/credentials')
        secret_value = response['SecretString']
        parsed_secret = json.loads(secret_value)
        bearer_token = parsed_secret['bearer_token']
        print("✅ Retrieved bearer token from Secrets Manager")
        
    except Exception as e:
        print(f"Error retrieving credentials: {e}")
        sys.exit(1)
    
    if not agent_arn or not bearer_token:
        print("Error: AGENT_ARN or BEARER_TOKEN not retrieved properly")
        sys.exit(1)
    
    # Construct MCP URL
    encoded_arn = agent_arn.replace(':', '%3A').replace('/', '%2F')
    mcp_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"
    
    print(f"\nConnecting to: {mcp_url}")

    try:
        # Create FastMCP client
        client = Client(
            transport=mcp_url,
            auth={
                "authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json"
            }
        )
        
        print("\n🔄 Connecting to MCP server...")
        await client.connect()
        print("✅ Connected to MCP server")
        
        print("\n🔄 Listing available tools...")
        tools = await client.list_tools()
        
        print("\n📋 Available Redshift MCP Tools:")
        print("=" * 50)
        for tool in tools:
            print(f"🔧 {tool.name}")
            print(f"   Description: {tool.description}")
            if hasattr(tool, 'inputSchema') and tool.inputSchema:
                properties = tool.inputSchema.get('properties', {})
                if properties:
                    print(f"   Parameters: {list(properties.keys())}")
            print()
        
        print(f"✅ Successfully connected to Redshift MCP server!")
        print(f"Found {len(tools)} tools available.")
        
        # Test list_clusters tool
        print("\n🧪 Testing list_clusters tool...")
        try:
            clusters_result = await client.call_tool("list_clusters", {})
            print(f"✅ list_clusters executed successfully")
            # Parse the result
            if hasattr(clusters_result, 'content') and clusters_result.content:
                result_text = clusters_result.content[0].text if clusters_result.content else str(clusters_result)
                print(f"Result preview: {result_text[:300]}...")
            else:
                print(f"Result: {str(clusters_result)[:300]}...")
        except Exception as e:
            print(f"⚠️  Error testing list_clusters: {e}")
        
        await client.disconnect()
        print("\n✅ Disconnected from MCP server")
        
    except Exception as e:
        print(f"❌ Error connecting to MCP server: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
