#!/usr/bin/env python3
"""Deploy Redshift MCP Server to AgentCore Runtime."""

import os
import sys
import time
import boto3
import json
from boto3.session import Session
from bedrock_agentcore_starter_toolkit import Runtime
from utils import setup_cognito_user_pool

def main():
    """Deploy the Redshift MCP server following the tutorial steps."""
    
    print("🚀 Starting Redshift MCP Server deployment to AgentCore Runtime")
    print("=" * 70)
    
    # Step 1: Check required files
    print("\n📋 Step 1: Checking required files...")
    required_files = ['redshift_mcp_server.py', 'requirements_redshift.txt']
    for file in required_files:
        if not os.path.exists(file):
            print(f"❌ Required file {file} not found")
            return 1
        print(f"✅ Found {file}")
    
    # Step 2: Setup AWS session and region
    print("\n🌍 Step 2: Setting up AWS session...")
    boto_session = Session()
    region = boto_session.region_name
    print(f"Using AWS region: {region}")
    
    # Step 3: Setup Cognito for authentication
    print("\n🔐 Step 3: Setting up Amazon Cognito user pool...")
    try:
        cognito_config = setup_cognito_user_pool()
        print("✅ Cognito setup completed")
        print(f"User Pool ID: {cognito_config.get('user_pool_id', 'N/A')}")
        print(f"Client ID: {cognito_config.get('client_id', 'N/A')}")
    except Exception as e:
        print(f"❌ Error setting up Cognito: {e}")
        return 1
    
    # Step 4: Configure AgentCore Runtime
    print("\n⚙️  Step 4: Configuring AgentCore Runtime...")
    try:
        agentcore_runtime = Runtime()
        
        auth_config = {
            "customJWTAuthorizer": {
                "allowedClients": [
                    cognito_config['client_id']
                ],
                "discoveryUrl": cognito_config['discovery_url'],
            }
        }
        
        print("Configuring AgentCore Runtime...")
        response = agentcore_runtime.configure(
            entrypoint="redshift_mcp_server.py",
            auto_create_execution_role=True,
            auto_create_ecr=True,
            requirements_file="requirements_redshift.txt",
            region=region,
            authorizer_configuration=auth_config,
            protocol="MCP",
            agent_name="redshift_mcp_server_agentcore"
        )
        print("✅ Configuration completed")
        
    except Exception as e:
        print(f"❌ Error configuring AgentCore Runtime: {e}")
        return 1
    
    # Step 5: Launch MCP server to AgentCore Runtime
    print("\n🚀 Step 5: Launching MCP server to AgentCore Runtime...")
    print("This may take several minutes...")
    try:
        launch_result = agentcore_runtime.launch()
        print("✅ Launch completed")
        print(f"Agent ARN: {launch_result.agent_arn}")
        print(f"Agent ID: {launch_result.agent_id}")
        
    except Exception as e:
        print(f"❌ Error launching AgentCore Runtime: {e}")
        return 1
    
    # Step 6: Check AgentCore Runtime status
    print("\n⏳ Step 6: Checking AgentCore Runtime status...")
    try:
        status_response = agentcore_runtime.status()
        status = status_response.endpoint['status']
        print(f"Initial status: {status}")
        
        end_status = ['READY', 'CREATE_FAILED', 'DELETE_FAILED', 'UPDATE_FAILED']
        while status not in end_status:
            print(f"Status: {status} - waiting...")
            time.sleep(10)
            status_response = agentcore_runtime.status()
            status = status_response.endpoint['status']
        
        if status == 'READY':
            print("✅ AgentCore Runtime is READY!")
        else:
            print(f"⚠️  AgentCore Runtime status: {status}")
            
        print(f"Final status: {status}")
        
    except Exception as e:
        print(f"❌ Error checking status: {e}")
        return 1
    
    # Step 7: Store configuration for remote access
    print("\n💾 Step 7: Storing configuration for remote access...")
    try:
        ssm_client = boto3.client('ssm', region_name=region)
        secrets_client = boto3.client('secretsmanager', region_name=region)

        # Payload the sales agent expects: client_id / username / password.
        # Bearer tokens are short-lived and minted on demand; do not persist them.
        secret_payload = json.dumps({
            "user_pool_id": cognito_config["user_pool_id"],
            "client_id": cognito_config["client_id"],
            "discovery_url": cognito_config["discovery_url"],
            "username": cognito_config["username"],
            "password": cognito_config["password"],
            "region": cognito_config["region"],
        })

        try:
            secrets_client.create_secret(
                Name='/mcp/redshift/credentials',
                Description='Cognito credentials for Redshift MCP server',
                SecretString=secret_payload
            )
            print("✅ Cognito credentials stored in Secrets Manager")
        except secrets_client.exceptions.ResourceExistsException:
            secrets_client.update_secret(
                SecretId='/mcp/redshift/credentials',
                SecretString=secret_payload
            )
            print("✅ Cognito credentials updated in Secrets Manager")

        ssm_client.put_parameter(
            Name='/mcp/redshift/agent_arn',
            Value=launch_result.agent_arn,
            Type='String',
            Description='Agent ARN for Redshift MCP server',
            Overwrite=True
        )
        print("✅ Agent ARN stored in Parameter Store")
        
        print("\n🎉 Configuration stored successfully!")
        print(f"Agent ARN: {launch_result.agent_arn}")
        
    except Exception as e:
        print(f"❌ Error storing configuration: {e}")
        return 1
    
    # Step 8: Create remote testing client
    print("\n🧪 Step 8: Creating remote testing client...")
    create_remote_client()
    
    print("\n🎉 Deployment completed successfully!")
    print("=" * 70)
    print("Your Redshift MCP server is now deployed and ready to use!")
    print(f"Agent ARN: {launch_result.agent_arn}")
    print("\nTo test the server, run:")
    print("python test_redshift_mcp_remote.py")
    
    return 0


def create_remote_client():
    """Create a remote testing client using FastMCP for the deployed MCP server."""
    
    client_code = '''#!/usr/bin/env python3
"""Test the deployed Redshift MCP server remotely using FastMCP client."""

import asyncio
import boto3
import json
import sys
from boto3.session import Session
from mcp.client.fastmcp import FastMCPClient

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
    
    print(f"\\nConnecting to: {mcp_url}")

    try:
        # Create FastMCP client
        client = FastMCPClient(
            url=mcp_url,
            headers={
                "authorization": f"Bearer {bearer_token}",
                "Content-Type": "application/json"
            }
        )
        
        print("\\n🔄 Connecting to MCP server...")
        await client.connect()
        print("✅ Connected to MCP server")
        
        print("\\n🔄 Listing available tools...")
        tools = await client.list_tools()
        
        print("\\n📋 Available Redshift MCP Tools:")
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
        print("\\n🧪 Testing list_clusters tool...")
        try:
            clusters_result = await client.call_tool("list_clusters", {})
            print(f"✅ list_clusters executed successfully")
            # Parse the result if it's a string
            if hasattr(clusters_result, 'content') and clusters_result.content:
                result_text = clusters_result.content[0].text if clusters_result.content else str(clusters_result)
                print(f"Result preview: {result_text[:200]}...")
            else:
                print(f"Result: {str(clusters_result)[:200]}...")
        except Exception as e:
            print(f"⚠️  Error testing list_clusters: {e}")
        
        await client.disconnect()
        print("\\n✅ Disconnected from MCP server")
        
    except Exception as e:
        print(f"❌ Error connecting to MCP server: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
'''
    
    with open('test_redshift_mcp_remote.py', 'w') as f:
        f.write(client_code)
    
    print("✅ Created test_redshift_mcp_remote.py")


if __name__ == "__main__":
    sys.exit(main())
