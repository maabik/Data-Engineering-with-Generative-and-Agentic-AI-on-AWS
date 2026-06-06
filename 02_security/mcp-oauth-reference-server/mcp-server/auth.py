"""JWT bearer-token validation against a Cognito user pool."""
from __future__ import annotations

import os
import time
from functools import lru_cache
from typing import Any

import httpx
from jose import jwt
from jose.exceptions import JWTError

ISSUER = os.environ["COGNITO_ISSUER"].rstrip("/")
RESOURCE_SERVER_ID = os.environ["MCP_RESOURCE_SERVER_IDENTIFIER"].rstrip("/")
JWKS_URL = f"{ISSUER}/.well-known/jwks.json"
_JWKS_TTL_SECONDS = 3600


class AuthError(Exception):
    def __init__(self, code: str, description: str, status: int = 401) -> None:
        super().__init__(description)
        self.code = code
        self.description = description
        self.status = status


@lru_cache(maxsize=1)
def _jwks_cache() -> dict[str, Any]:
    return {"fetched_at": 0.0, "keys": {}}


def _load_jwks() -> dict[str, Any]:
    cache = _jwks_cache()
    if time.time() - cache["fetched_at"] < _JWKS_TTL_SECONDS and cache["keys"]:
        return cache["keys"]
    resp = httpx.get(JWKS_URL, timeout=5.0)
    resp.raise_for_status()
    cache["keys"] = {key["kid"]: key for key in resp.json()["keys"]}
    cache["fetched_at"] = time.time()
    return cache["keys"]


def validate_bearer(token: str, required_scope: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise AuthError("invalid_token", str(exc)) from exc

    keys = _load_jwks()
    key = keys.get(header.get("kid"))
    if key is None:
        raise AuthError("invalid_token", "unknown key id")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[header.get("alg", "RS256")],
            issuer=ISSUER,
            options={"verify_aud": False},  # Cognito access tokens omit aud
        )
    except JWTError as exc:
        raise AuthError("invalid_token", str(exc)) from exc

    if claims.get("token_use") != "access":
        raise AuthError("invalid_token", "not an access token")

    scopes = set((claims.get("scope") or "").split())
    fq_scope = f"{RESOURCE_SERVER_ID}/{required_scope}"
    if fq_scope not in scopes:
        raise AuthError("insufficient_scope", f"requires {fq_scope}", status=403)

    return claims
