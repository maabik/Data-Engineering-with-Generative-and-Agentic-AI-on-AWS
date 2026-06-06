"""Cognito setup + SigV4 fallback used by the KG MCP server.

Mirrors ``12_agentic/mcp/02-hosting-MCP-server/utils.py`` so the book's
audience sees a single auth pattern across MCP servers in the repo.

Required env vars:
    KG_COGNITO_PASSWORD  permanent password (min 8 chars)
Optional env vars:
    KG_COGNITO_USERNAME  defaults to "kguser"
    AWS_REGION           defaults to boto3 resolved region, then us-east-1
"""

from __future__ import annotations

import os
from typing import Any

import boto3

POOL_NAME = "KGAgentMCPUserPool"
CLIENT_NAME = "KGAgentMCPClient"


def _resolve_region() -> str:
    return os.environ.get("AWS_REGION") or boto3.Session().region_name or "us-east-1"


def _find_pool(client, name: str):
    paginator = client.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for pool in page.get("UserPools", []):
            if pool["Name"] == name:
                return pool["Id"]
    return None


def _find_client(client, pool_id: str, name: str):
    paginator = client.get_paginator("list_user_pool_clients")
    for page in paginator.paginate(UserPoolId=pool_id, MaxResults=60):
        for c in page.get("UserPoolClients", []):
            if c["ClientName"] == name:
                return c["ClientId"]
    return None


def setup_cognito_user_pool() -> dict[str, Any]:
    password = os.environ.get("KG_COGNITO_PASSWORD")
    if not password:
        raise RuntimeError(
            "KG_COGNITO_PASSWORD env var is required; refusing to use a hardcoded default."
        )
    username = os.environ.get("KG_COGNITO_USERNAME", "kguser")
    region = _resolve_region()

    cognito = boto3.client("cognito-idp", region_name=region)
    pool_id = _find_pool(cognito, POOL_NAME)
    if pool_id:
        print(f"Reusing existing user pool: {pool_id}")
    else:
        pool_id = cognito.create_user_pool(
            PoolName=POOL_NAME,
            Policies={"PasswordPolicy": {"MinimumLength": 8}},
            AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
        )["UserPool"]["Id"]
        print(f"Created user pool: {pool_id}")

    client_id = _find_client(cognito, pool_id, CLIENT_NAME)
    if client_id:
        print(f"Reusing existing client: {client_id}")
    else:
        client_id = cognito.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName=CLIENT_NAME,
            GenerateSecret=False,
            ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        )["UserPoolClient"]["ClientId"]
        print(f"Created client: {client_id}")

    try:
        cognito.admin_create_user(
            UserPoolId=pool_id,
            Username=username,
            TemporaryPassword=password,
            MessageAction="SUPPRESS",
        )
    except cognito.exceptions.UsernameExistsException:
        pass

    cognito.admin_set_user_password(
        UserPoolId=pool_id,
        Username=username,
        Password=password,
        Permanent=True,
    )

    auth = cognito.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
    )
    bearer_token = auth["AuthenticationResult"]["AccessToken"]

    discovery_url = (
        f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
    )

    return {
        "user_pool_id": pool_id,
        "client_id": client_id,
        "discovery_url": discovery_url,
        "bearer_token": bearer_token,
        "username": username,
        "password": password,
        "region": region,
    }


def get_bearer_token() -> str:
    """Mint a fresh bearer token from Cognito using stored credentials.

    Used by MCP clients (the agent) at runtime — bearer tokens are
    short-lived so we don't persist them.
    """
    import json

    region = _resolve_region()
    secret_id = os.environ.get("KG_MCP_CREDENTIALS_SECRET", "/kg/mcp/credentials")
    secrets = boto3.client("secretsmanager", region_name=region)
    creds = json.loads(secrets.get_secret_value(SecretId=secret_id)["SecretString"])

    cognito = boto3.client("cognito-idp", region_name=region)
    auth = cognito.initiate_auth(
        ClientId=creds["client_id"],
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": creds["username"], "PASSWORD": creds["password"]},
    )
    return auth["AuthenticationResult"]["AccessToken"]
