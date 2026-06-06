#!/usr/bin/env python3
"""Test the deployed Redshift MCP server remotely using standard MCP client."""

import asyncio
import boto3
import json
import sys
from boto3.session import Session
from datetime import timedelta

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

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
    headers = {
        "authorization": f"Bearer {bearer_token}",
        "Content-Type": "application/json"
    }
    
    print(f"\nConnecting to: {mcp_url}")
    print("Headers configured ✅")

    try:
        async with streamablehttp_client(mcp_url, headers, timeout=timedelta(seconds=120), terminate_on_close=False) as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                print("\n🔄 Initializing MCP session...")
                await session.initialize()
                print("✅ MCP session initialized")
                
                print("\n🔄 Listing available tools...")
                tool_result = await session.list_tools()
                
                print("\n📋 Available Redshift MCP Tools:")
                print("=" * 50)
                for tool in tool_result.tools:
                    print(f"🔧 {tool.name}")
                    print(f"   Description: {tool.description}")
                    if hasattr(tool, 'inputSchema') and tool.inputSchema:
                        properties = tool.inputSchema.get('properties', {})
                        if properties:
                            print(f"   Parameters: {list(properties.keys())}")
                    print()
                
                print(f"✅ Successfully connected to Redshift MCP server!")
                print(f"Found {len(tool_result.tools)} tools available.")
                
                # Test list_clusters tool
                print("\n🧪 Testing list_clusters tool...")
                try:
                    clusters_result = await session.call_tool(
                        name="list_clusters",
                        arguments={}
                    )
                    print(f"✅ list_clusters executed successfully")
                    if hasattr(clusters_result, 'content') and clusters_result.content:
                        result_text = clusters_result.content[0].text if clusters_result.content else str(clusters_result)
                        print(f"Result preview: {result_text[:500]}...")
                    else:
                        print(f"Result: {str(clusters_result)[:500]}...")
                except Exception as e:
                    print(f"⚠️  Error testing list_clusters: {e}")
                
    except Exception as e:
        print(f"❌ Error connecting to MCP server: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
