#!/usr/bin/env python3
"""Teardown all AWS resources created by deploy_redshift_mcp.py.

Deletes (in order, idempotent):
  1. AgentCore Runtime
  2. ECR repository
  3. SSM Parameter Store parameter
  4. Cognito user pool (read from Secrets Manager before secret deletion)
  5. Secrets Manager secret
"""

import json
import sys

import boto3
from boto3.session import Session


SECRET_ID = "/mcp/redshift/credentials"
SSM_PARAM = "/mcp/redshift/agent_arn"
AGENT_NAME = "redshift_mcp_server_agentcore"
ECR_REPO = "bedrock-agentcore-redshift_mcp_server_agentcore"


def main():
    boto_session = Session()
    region = boto_session.region_name
    print(f"🌍 Region: {region}")

    agentcore_control = boto3.client("bedrock-agentcore-control", region_name=region)
    ecr_client = boto3.client("ecr", region_name=region)
    ssm_client = boto3.client("ssm", region_name=region)
    secrets_client = boto3.client("secretsmanager", region_name=region)
    cognito_client = boto3.client("cognito-idp", region_name=region)

    # Read Cognito pool ID from Secrets Manager BEFORE deleting the secret.
    user_pool_id = None
    try:
        secret_value = secrets_client.get_secret_value(SecretId=SECRET_ID)
        cognito_config = json.loads(secret_value["SecretString"])
        user_pool_id = cognito_config.get("user_pool_id")
        print(f"🔎 Found Cognito user pool in secret: {user_pool_id}")
    except secrets_client.exceptions.ResourceNotFoundException:
        print(f"ℹ️  Secret {SECRET_ID} not found; will skip Cognito pool delete")

    # 1. AgentCore Runtime
    try:
        print("Deleting AgentCore Runtime...")
        runtimes = agentcore_control.list_agent_runtimes().get("agentRuntimes", [])
        target = next((r for r in runtimes if r.get("agentRuntimeName") == AGENT_NAME), None)
        if target:
            agentcore_control.delete_agent_runtime(agentRuntimeId=target["agentRuntimeId"])
            print(f"✓ AgentCore Runtime {target['agentRuntimeId']} deletion initiated")
        else:
            print(f"ℹ️  No runtime named {AGENT_NAME} found")
    except Exception as e:
        print(f"⚠️  AgentCore Runtime delete failed: {e}")

    # 2. ECR repository
    try:
        print(f"Deleting ECR repository {ECR_REPO}...")
        ecr_client.delete_repository(repositoryName=ECR_REPO, force=True)
        print("✓ ECR repository deleted")
    except ecr_client.exceptions.RepositoryNotFoundException:
        print("ℹ️  ECR repository not found")
    except Exception as e:
        print(f"⚠️  ECR delete failed: {e}")

    # 3. SSM parameter
    try:
        ssm_client.delete_parameter(Name=SSM_PARAM)
        print(f"✓ SSM parameter {SSM_PARAM} deleted")
    except ssm_client.exceptions.ParameterNotFound:
        print(f"ℹ️  SSM parameter {SSM_PARAM} not found")

    # 4. Cognito user pool (deletes pool and all clients in it)
    if user_pool_id:
        try:
            print(f"Deleting Cognito user pool {user_pool_id}...")
            cognito_client.delete_user_pool(UserPoolId=user_pool_id)
            print("✓ Cognito user pool deleted")
        except cognito_client.exceptions.ResourceNotFoundException:
            print("ℹ️  Cognito user pool not found")
        except Exception as e:
            print(f"⚠️  Cognito user pool delete failed: {e}")
    else:
        print("⚠️  Cognito user pool ID unknown — delete it manually if it still exists")

    # 5. Secrets Manager secret (last, since we read from it)
    try:
        secrets_client.delete_secret(SecretId=SECRET_ID, ForceDeleteWithoutRecovery=True)
        print(f"✓ Secrets Manager secret {SECRET_ID} deleted")
    except secrets_client.exceptions.ResourceNotFoundException:
        print(f"ℹ️  Secrets Manager secret {SECRET_ID} not found")

    print("\n✅ Teardown complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
