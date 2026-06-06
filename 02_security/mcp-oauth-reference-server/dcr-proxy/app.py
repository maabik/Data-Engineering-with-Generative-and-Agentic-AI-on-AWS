"""RFC 7591 Dynamic Client Registration proxy in front of Cognito.

Cognito user pools don't implement DCR natively. This service accepts a
standards-compliant registration request and maps it onto
AdminCreateUserPoolClient, returning a spec-shaped response so MCP clients that
expect RFC 7591 work unmodified.

Also serves RFC 8414 authorization-server metadata and RFC 9728
protected-resource metadata, which are the discovery documents MCP clients
fetch to learn where to register and where to send users for consent.
"""
from __future__ import annotations

import os
import secrets
from typing import Any

import boto3
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

USER_POOL_ID = os.environ["COGNITO_USER_POOL_ID"]
REGION = os.environ["AWS_REGION"]
HOSTED_DOMAIN = os.environ["COGNITO_HOSTED_DOMAIN"].rstrip("/")
RESOURCE_SERVER_ID = os.environ["MCP_RESOURCE_SERVER_IDENTIFIER"].rstrip("/")
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"

DEFAULT_SCOPES = [
    "openid",
    "email",
    f"{RESOURCE_SERVER_ID}/tools.read",
    f"{RESOURCE_SERVER_ID}/tools.write",
]

cognito = boto3.client("cognito-idp", region_name=REGION)
app = FastAPI(title="MCP DCR Proxy")


class RegistrationRequest(BaseModel):
    client_name: str = Field(..., max_length=128)
    redirect_uris: list[str]
    grant_types: list[str] = Field(default_factory=lambda: ["authorization_code", "refresh_token"])
    token_endpoint_auth_method: str = "none"  # public client = PKCE
    scope: str | None = None


@app.get("/.well-known/oauth-authorization-server")
def as_metadata() -> dict[str, Any]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{HOSTED_DOMAIN}/oauth2/authorize",
        "token_endpoint": f"{HOSTED_DOMAIN}/oauth2/token",
        "registration_endpoint": "/register",
        "jwks_uri": f"{ISSUER}/.well-known/jwks.json",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
        "scopes_supported": DEFAULT_SCOPES,
    }


@app.get("/.well-known/oauth-protected-resource")
def pr_metadata() -> dict[str, Any]:
    return {
        "resource": RESOURCE_SERVER_ID,
        "authorization_servers": [ISSUER],
        "scopes_supported": [
            f"{RESOURCE_SERVER_ID}/tools.read",
            f"{RESOURCE_SERVER_ID}/tools.write",
        ],
        "bearer_methods_supported": ["header"],
    }


@app.post("/register", status_code=201)
def register(req: RegistrationRequest) -> dict[str, Any]:
    requested_scopes = req.scope.split() if req.scope else DEFAULT_SCOPES
    is_public = req.token_endpoint_auth_method == "none"

    try:
        resp = cognito.create_user_pool_client(
            UserPoolId=USER_POOL_ID,
            ClientName=f"{req.client_name}-{secrets.token_hex(4)}"[:128],
            GenerateSecret=not is_public,
            AllowedOAuthFlowsUserPoolClient=True,
            AllowedOAuthFlows=["code"],
            AllowedOAuthScopes=requested_scopes,
            CallbackURLs=req.redirect_uris,
            SupportedIdentityProviders=["COGNITO"],
            ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"],
            EnableTokenRevocation=True,
            PreventUserExistenceErrors="ENABLED",
        )
    except cognito.exceptions.ClientError as exc:  # type: ignore[attr-defined]
        raise HTTPException(400, str(exc)) from exc

    client = resp["UserPoolClient"]
    body: dict[str, Any] = {
        "client_id": client["ClientId"],
        "client_name": client["ClientName"],
        "redirect_uris": req.redirect_uris,
        "grant_types": req.grant_types,
        "token_endpoint_auth_method": req.token_endpoint_auth_method,
        "scope": " ".join(requested_scopes),
        "registration_access_token": secrets.token_urlsafe(32),
        "registration_client_uri": f"/register/{client['ClientId']}",
    }
    if not is_public:
        body["client_secret"] = client["ClientSecret"]
    return body


@app.delete("/register/{client_id}", status_code=204)
def deregister(client_id: str) -> None:
    try:
        cognito.delete_user_pool_client(UserPoolId=USER_POOL_ID, ClientId=client_id)
    except cognito.exceptions.ResourceNotFoundException:  # type: ignore[attr-defined]
        raise HTTPException(404, "client not found")
