#!/usr/bin/env python3
"""Deploy the KG MCP server to Bedrock AgentCore Runtime.

Mirrors ``12_agentic/mcp/02-hosting-MCP-server/deploy_redshift_mcp.py``:
  - sets up Cognito (idempotent),
  - configures and launches AgentCore Runtime,
  - waits for READY,
  - stores the agent ARN in Parameter Store and Cognito creds in Secrets Manager.

Usage:

    cd src/knowledge_graph_agent/mcp_server
    KG_COGNITO_PASSWORD=ChangeMe1! python deploy.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import boto3
from boto3.session import Session


PROJECT_ROOT = Path(__file__).resolve().parents[3]  # knowledge-graph-agent/
DEPLOY_DIR = PROJECT_ROOT / ".agentcore-build"


def _build_deploy_dir() -> Path:
    """Stage a flat directory the toolkit can build a container from.

    Layout:
        .agentcore-build/
            server.py        ← imports knowledge_graph_agent.mcp_server.tools
            knowledge_graph_agent/        ← copied from src/knowledge_graph_agent
            requirements.txt
    """
    if DEPLOY_DIR.exists():
        shutil.rmtree(DEPLOY_DIR)
    DEPLOY_DIR.mkdir()
    src_pkg = PROJECT_ROOT / "src" / "knowledge_graph_agent"
    shutil.copytree(src_pkg, DEPLOY_DIR / "knowledge_graph_agent", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy(src_pkg / "mcp_server" / "server.py", DEPLOY_DIR / "server.py")
    shutil.copy(PROJECT_ROOT / "requirements.txt", DEPLOY_DIR / "requirements.txt")
    return DEPLOY_DIR


def main() -> int:
    print("🚀 Deploying KG MCP server to Bedrock AgentCore Runtime")
    print("=" * 70)

    # Lazy import — the toolkit may not be installed in dev environments.
    try:
        from bedrock_agentcore_starter_toolkit import Runtime
    except ImportError:
        print("❌ bedrock-agentcore-starter-toolkit is not installed.")
        print("   pip install bedrock-agentcore-starter-toolkit")
        return 1

    from knowledge_graph_agent.mcp_server.auth import setup_cognito_user_pool

    print("\n🧱 Staging container build directory...")
    deploy_dir = _build_deploy_dir()
    server_path = deploy_dir / "server.py"
    requirements = deploy_dir / "requirements.txt"
    print(f"   build dir:    {deploy_dir}")
    print(f"   entrypoint:   {server_path}")
    if not server_path.exists():
        print(f"❌ {server_path} not found")
        return 1
    if not requirements.exists():
        print(f"❌ {requirements} not found")
        return 1
    # Switch CWD to the deploy dir so the toolkit's relative paths resolve.
    os.chdir(deploy_dir)

    region = os.environ.get("AWS_REGION") or Session().region_name or "us-east-1"
    print(f"AWS region: {region}")

    print("\n🔐 Configuring Cognito user pool...")
    cognito = setup_cognito_user_pool()
    print(f"User Pool: {cognito['user_pool_id']}")
    print(f"Client:    {cognito['client_id']}")

    print("\n⚙️  Configuring AgentCore Runtime...")
    runtime = Runtime()
    auth_config = {
        "customJWTAuthorizer": {
            "allowedClients": [cognito["client_id"]],
            "discoveryUrl": cognito["discovery_url"],
        }
    }
    runtime.configure(
        entrypoint="server.py",
        auto_create_execution_role=True,
        auto_create_ecr=True,
        requirements_file="requirements.txt",
        region=region,
        authorizer_configuration=auth_config,
        protocol="MCP",
        agent_name="knowledge_graph_agent_mcp_server",
    )

    # Pass the graph + vector-bucket config through to the runtime as
    # env vars (the same KG_* vars that drive the local package).
    ssm = boto3.client("ssm", region_name=region)
    runtime_env = {
        "KG_REGION": region,
        "KG_GRAPH_ID": ssm.get_parameter(Name="/kg/graph_id")["Parameter"]["Value"],
        "KG_S3_VECTORS_BUCKET": ssm.get_parameter(Name="/kg/vectors_bucket")["Parameter"]["Value"],
    }
    print(f"\n   runtime env:  {runtime_env}")

    print("\n🚀 Launching MCP server (this takes several minutes)...")
    launch = runtime.launch(env_vars=runtime_env, auto_update_on_conflict=True)
    agent_arn = launch.agent_arn
    print(f"Agent ARN: {agent_arn}")

    print("\n⏳ Waiting for READY...")
    end_states = {"READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"}
    while True:
        status = runtime.status().endpoint["status"]
        print(f"  status: {status}")
        if status in end_states:
            break
        time.sleep(10)
    if status != "READY":
        print(f"❌ Runtime did not reach READY (final: {status})")
        return 1
    print("✅ Runtime is READY")

    print("\n💾 Storing config in Parameter Store + Secrets Manager...")
    ssm = boto3.client("ssm", region_name=region)
    secrets = boto3.client("secretsmanager", region_name=region)
    ssm.put_parameter(
        Name="/kg/mcp/agent_arn",
        Value=agent_arn,
        Type="String",
        Overwrite=True,
        Description="KG MCP server AgentCore ARN",
    )
    secret_payload = json.dumps(
        {
            "user_pool_id": cognito["user_pool_id"],
            "client_id": cognito["client_id"],
            "discovery_url": cognito["discovery_url"],
            "username": cognito["username"],
            "password": cognito["password"],
            "region": cognito["region"],
        }
    )
    try:
        secrets.create_secret(
            Name="/kg/mcp/credentials",
            Description="Cognito credentials for KG MCP server",
            SecretString=secret_payload,
        )
        print("✅ Created /kg/mcp/credentials")
    except secrets.exceptions.ResourceExistsException:
        secrets.update_secret(SecretId="/kg/mcp/credentials", SecretString=secret_payload)
        print("✅ Updated /kg/mcp/credentials")

    print("\n🎉 KG MCP server deployed.")
    print(f"   Agent ARN:   {agent_arn}")
    print(f"   Region:      {region}")
    print(f"   Cognito:     {cognito['user_pool_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
