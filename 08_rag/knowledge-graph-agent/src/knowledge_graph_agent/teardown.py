#!/usr/bin/env python3
"""Tear down the runtime resources the toolkit creates outside CDK.

The toolkit-managed pieces (AgentCore Runtime, ECR repository, CodeBuild
project, IAM execution roles, Cognito user pool) live outside the CDK
stacks, so ``cdk destroy`` doesn't touch them. This module deletes them
in dependency order.

Idempotent: every step swallows "not found" and "already deleted" errors.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Iterable

import boto3
from botocore.exceptions import ClientError

DEFAULT_REGION = os.environ.get("AWS_REGION") or "us-east-1"

AGENT_RUNTIME_NAMES = ("knowledge_graph_agent_mcp_server", "knowledge_graph_agent_strands")
AGENT_ARN_PARAMS = ("/kg/mcp/agent_arn", "/kg/agent/agent_arn")
ECR_REPOSITORIES = (
    "bedrock-agentcore-knowledge_graph_agent_mcp_server",
    "bedrock-agentcore-knowledge_graph_agent_strands",
)
CODEBUILD_PROJECTS = (
    "bedrock-agentcore-knowledge_graph_agent_mcp_server-builder",
    "bedrock-agentcore-knowledge_graph_agent_strands-builder",
)
COGNITO_POOL_NAME = "KGAgentMCPUserPool"
SECRETS = ("/kg/mcp/credentials",)
SSM_PARAMS = (
    "/kg/mcp/agent_arn",
    "/kg/agent/agent_arn",
    "/kg/graph_id",
    "/kg/vectors_bucket",
    "/kg/documents_bucket",
    "/kg/agent_role_arn",
)


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _skip(msg: str) -> None:
    print(f"  ⏭  {msg}")


def _err(msg: str) -> None:
    print(f"  ⚠️  {msg}")


def delete_agent_runtimes(region: str) -> None:
    print("🧨 Deleting AgentCore Runtimes...")
    client = boto3.client("bedrock-agentcore-control", region_name=region)
    # The runtime list returns full IDs; match by name prefix.
    paginator = client.get_paginator("list_agent_runtimes")
    targets: list[str] = []
    for page in paginator.paginate():
        for r in page.get("agentRuntimes", []):
            if any(r["agentRuntimeName"].startswith(name) for name in AGENT_RUNTIME_NAMES):
                targets.append(r["agentRuntimeId"])
    if not targets:
        _skip("no matching runtimes")
        return
    for rid in targets:
        try:
            client.delete_agent_runtime(agentRuntimeId=rid)
            _ok(f"deleted {rid}")
        except ClientError as exc:
            _err(f"{rid}: {exc.response.get('Error', {}).get('Code')}")


def delete_ecr_repositories(region: str) -> None:
    print("🧨 Deleting ECR repositories...")
    ecr = boto3.client("ecr", region_name=region)
    for name in ECR_REPOSITORIES:
        try:
            ecr.delete_repository(repositoryName=name, force=True)
            _ok(f"deleted {name}")
        except ecr.exceptions.RepositoryNotFoundException:
            _skip(f"{name} not found")
        except ClientError as exc:
            _err(f"{name}: {exc.response.get('Error', {}).get('Code')}")


def delete_codebuild_projects(region: str) -> None:
    print("🧨 Deleting CodeBuild projects...")
    cb = boto3.client("codebuild", region_name=region)
    for name in CODEBUILD_PROJECTS:
        try:
            cb.delete_project(name=name)
            _ok(f"deleted {name}")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("ResourceNotFoundException", "InvalidInputException"):
                _skip(f"{name} not found")
            else:
                _err(f"{name}: {code}")


def delete_cognito_pool(region: str) -> None:
    print("🧨 Deleting Cognito user pool...")
    cognito = boto3.client("cognito-idp", region_name=region)
    paginator = cognito.get_paginator("list_user_pools")
    pool_id: str | None = None
    for page in paginator.paginate(MaxResults=60):
        for pool in page.get("UserPools", []):
            if pool["Name"] == COGNITO_POOL_NAME:
                pool_id = pool["Id"]
                break
        if pool_id:
            break
    if not pool_id:
        _skip(f"pool {COGNITO_POOL_NAME!r} not found")
        return
    # Domain (if any) blocks deletion; remove it first.
    try:
        info = cognito.describe_user_pool(UserPoolId=pool_id).get("UserPool", {})
        domain = info.get("Domain")
        if domain:
            cognito.delete_user_pool_domain(Domain=domain, UserPoolId=pool_id)
            _ok(f"deleted domain {domain}")
    except ClientError:
        pass
    try:
        cognito.delete_user_pool(UserPoolId=pool_id)
        _ok(f"deleted pool {pool_id}")
    except ClientError as exc:
        _err(f"{pool_id}: {exc.response.get('Error', {}).get('Code')}")


def delete_s3_vectors(region: str) -> None:
    print("🧨 Deleting S3 Vectors index + bucket...")
    ssm = boto3.client("ssm", region_name=region)
    try:
        bucket = ssm.get_parameter(Name="/kg/vectors_bucket")["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        _skip("/kg/vectors_bucket not in SSM")
        return
    s3v = boto3.client("s3vectors", region_name=region)
    index = os.environ.get("KG_S3_VECTORS_INDEX", "kg-entities")
    try:
        s3v.delete_index(vectorBucketName=bucket, indexName=index)
        _ok(f"deleted index {index}")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("ResourceNotFoundException", "NotFoundException"):
            _skip(f"index {index} not found")
        else:
            _err(f"{index}: {code}")
    try:
        s3v.delete_vector_bucket(vectorBucketName=bucket)
        _ok(f"deleted bucket {bucket}")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("ResourceNotFoundException", "NotFoundException"):
            _skip(f"bucket {bucket} not found")
        else:
            _err(f"{bucket}: {code}")


def delete_secrets(region: str) -> None:
    print("🧨 Deleting Secrets Manager entries...")
    secrets = boto3.client("secretsmanager", region_name=region)
    for name in SECRETS:
        try:
            secrets.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
            _ok(f"deleted {name}")
        except secrets.exceptions.ResourceNotFoundException:
            _skip(f"{name} not found")
        except ClientError as exc:
            _err(f"{name}: {exc.response.get('Error', {}).get('Code')}")


def delete_ssm_params(region: str) -> None:
    print("🧨 Deleting orphan SSM parameters...")
    ssm = boto3.client("ssm", region_name=region)
    # Most are managed by CDK and disappear with `cdk destroy`; we clean
    # the ones the toolkit/runtime created out-of-band so a fresh redeploy
    # doesn't read stale values.
    standalone = ("/kg/mcp/agent_arn", "/kg/agent/agent_arn")
    for name in standalone:
        try:
            ssm.delete_parameter(Name=name)
            _ok(f"deleted {name}")
        except ssm.exceptions.ParameterNotFound:
            _skip(f"{name} not found")
        except ClientError as exc:
            _err(f"{name}: {exc.response.get('Error', {}).get('Code')}")


def delete_iam_roles(region: str) -> None:
    """Detach inline + managed policies and delete the toolkit-created
    AgentCore execution roles. CDK doesn't own these — the AgentCore
    starter toolkit creates them with auto_create_execution_role=True.
    """
    print("🧨 Deleting AgentCore IAM execution roles...")
    iam = boto3.client("iam")
    role_prefixes = (
        "AmazonBedrockAgentCoreSDKRuntime-",
        "AmazonBedrockAgentCoreSDKCodeBuild-",
    )
    paginator = iam.get_paginator("list_roles")
    for page in paginator.paginate():
        for r in page.get("Roles", []):
            name = r["RoleName"]
            if not any(name.startswith(p) for p in role_prefixes):
                continue
            # Best-effort: detach inline + managed policies, then delete.
            try:
                for inline in iam.list_role_policies(RoleName=name).get("PolicyNames", []):
                    iam.delete_role_policy(RoleName=name, PolicyName=inline)
                for attached in iam.list_attached_role_policies(RoleName=name).get(
                    "AttachedPolicies", []
                ):
                    iam.detach_role_policy(RoleName=name, PolicyArn=attached["PolicyArn"])
                iam.delete_role(RoleName=name)
                _ok(f"deleted {name}")
            except ClientError as exc:
                _err(f"{name}: {exc.response.get('Error', {}).get('Code')}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--keep-iam",
        action="store_true",
        help="Skip deleting toolkit-created IAM roles (useful when other agents in the account share them).",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    region = args.region

    print(f"Region: {region}\n")
    delete_agent_runtimes(region)
    print()
    delete_codebuild_projects(region)
    print()
    delete_ecr_repositories(region)
    print()
    delete_s3_vectors(region)
    print()
    delete_secrets(region)
    print()
    delete_ssm_params(region)
    print()
    delete_cognito_pool(region)
    print()
    if not args.keep_iam:
        delete_iam_roles(region)
    print("\n✅ Toolkit-created resources cleaned up.")
    print("   Now run `cdk destroy` to remove the CDK stacks (Neptune, Lambda, S3, KMS).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
