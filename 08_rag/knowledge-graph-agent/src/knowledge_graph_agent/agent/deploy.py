#!/usr/bin/env python3
"""Deploy the KG Strands agent to a separate AgentCore Runtime.

This is the agent's *own* runtime, distinct from the MCP server. The
agent makes outbound MCP calls to ``/kg/mcp/agent_arn`` (the MCP server)
to traverse the knowledge graph. Splitting agent and MCP server into two
runtimes mirrors the chapter-12 pattern (sales-reporting-agent →
redshift-mcp-server).

Usage:
    AWS_REGION=us-east-1 python -m knowledge_graph_agent.agent.deploy
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import boto3


PROJECT_ROOT = Path(__file__).resolve().parents[3]  # knowledge-graph-agent/
DEPLOY_DIR = PROJECT_ROOT / ".agent-build"


def _build_deploy_dir() -> Path:
    """Stage a flat container build dir.

    The AgentCore toolkit builds an image rooted at the entrypoint's
    parent directory; copying knowledge_graph_agent next to the entrypoint puts the
    package on sys.path inside the container.
    """
    if DEPLOY_DIR.exists():
        shutil.rmtree(DEPLOY_DIR)
    DEPLOY_DIR.mkdir()
    src_pkg = PROJECT_ROOT / "src" / "knowledge_graph_agent"
    shutil.copytree(
        src_pkg,
        DEPLOY_DIR / "knowledge_graph_agent",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy(src_pkg / "agent" / "knowledge_graph_agent.py", DEPLOY_DIR / "agent.py")
    shutil.copy(PROJECT_ROOT / "requirements.txt", DEPLOY_DIR / "requirements.txt")
    return DEPLOY_DIR


def main() -> int:
    print("🚀 Deploying KG Strands agent to Bedrock AgentCore Runtime")
    print("=" * 70)

    try:
        from bedrock_agentcore_starter_toolkit import Runtime
    except ImportError:
        print("❌ bedrock-agentcore-starter-toolkit not installed.")
        return 1

    print("\n🧱 Staging container build directory...")
    deploy_dir = _build_deploy_dir()
    print(f"   build dir:    {deploy_dir}")

    region = os.environ.get("AWS_REGION") or boto3.Session().region_name or "us-east-1"
    print(f"AWS region: {region}")
    os.chdir(deploy_dir)

    # The agent doesn't need its own Cognito — it's invoked via SigV4 on
    # the AgentCore Runtime control plane. Outbound MCP calls reuse the
    # /kg/mcp/credentials secret already in place.
    print("\n⚙️  Configuring AgentCore Runtime...")
    runtime = Runtime()
    runtime.configure(
        entrypoint="agent.py",
        auto_create_execution_role=True,
        auto_create_ecr=True,
        requirements_file="requirements.txt",
        region=region,
        protocol="HTTP",  # entrypoint pattern, not MCP server
        agent_name="knowledge_graph_agent_strands",
    )

    ssm = boto3.client("ssm", region_name=region)
    runtime_env = {
        "AWS_REGION": region,
        "KG_REGION": region,
        "KG_GRAPH_ID": ssm.get_parameter(Name="/kg/graph_id")["Parameter"]["Value"],
        "KG_S3_VECTORS_BUCKET": ssm.get_parameter(Name="/kg/vectors_bucket")["Parameter"]["Value"],
        "KG_MCP_AGENT_ARN_PARAM": "/kg/mcp/agent_arn",
        "KG_MCP_CREDENTIALS_SECRET": "/kg/mcp/credentials",
    }
    print(f"   runtime env:  {runtime_env}")

    print("\n🚀 Launching agent runtime...")
    launch = runtime.launch(env_vars=runtime_env, auto_update_on_conflict=True)
    agent_arn = launch.agent_arn
    print(f"Agent ARN: {agent_arn}")

    print("\n⏳ Waiting for READY...")
    end = {"READY", "CREATE_FAILED", "DELETE_FAILED", "UPDATE_FAILED"}
    while True:
        status = runtime.status().endpoint["status"]
        print(f"  status: {status}")
        if status in end:
            break
        time.sleep(10)
    if status != "READY":
        print(f"❌ Runtime did not reach READY (final: {status})")
        return 1

    # Persist the agent ARN so callers (run_agent.py --mode remote) can
    # find it without env-var setup.
    ssm.put_parameter(
        Name="/kg/agent/agent_arn",
        Value=agent_arn,
        Type="String",
        Overwrite=True,
        Description="KG Strands agent AgentCore ARN",
    )
    print("\n💾 Stored /kg/agent/agent_arn in Parameter Store")

    print("\n🎉 KG agent deployed.")
    print(f"   Agent ARN: {agent_arn}")
    print(f"   Region:    {region}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
