"""MCP Streamable HTTP resource server with OAuth 2.1 bearer-token auth.

Validates every request's bearer token against the Cognito JWKS, checks scopes
per tool, and returns RFC 9728 `WWW-Authenticate` challenges pointing to the
protected-resource metadata so clients can complete the OAuth dance.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from auth import AuthError, validate_bearer

PROTECTED_RESOURCE_METADATA_URL = os.environ.get(
    "MCP_PROTECTED_RESOURCE_METADATA_URL",
    "http://localhost:8080/.well-known/oauth-protected-resource",
)

app = FastAPI(title="MCP Resource Server")

# Scope required per tool. Mirrors the scopes declared on the Cognito resource
# server in infra/cognito.yaml.
TOOL_SCOPES: dict[str, str] = {
    "echo": "tools.read",
    "whoami": "tools.read",
    "write_note": "tools.write",
}


def _challenge(err: AuthError) -> JSONResponse:
    value = (
        f'Bearer error="{err.code}", '
        f'error_description="{err.description}", '
        f'resource_metadata="{PROTECTED_RESOURCE_METADATA_URL}"'
    )
    return JSONResponse(
        status_code=err.status,
        content={"error": err.code, "error_description": err.description},
        headers={"WWW-Authenticate": value},
    )


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("invalid_request", "missing bearer token")
    return authorization.split(" ", 1)[1].strip()


def _jsonrpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


@app.post("/mcp")
async def mcp_endpoint(request: Request, authorization: str | None = Header(default=None)):
    body = await request.json()
    method = body.get("method")
    params = body.get("params") or {}
    req_id = body.get("id")

    # `initialize` is the only method reachable without a token; clients need
    # it to discover capabilities before authenticating.
    if method == "initialize":
        return _jsonrpc_result(
            req_id,
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mcp-oauth-reference", "version": "0.1.0"},
            },
        )

    try:
        token = _extract_bearer(authorization)
    except AuthError as err:
        return _challenge(err)

    if method == "tools/list":
        try:
            validate_bearer(token, "tools.read")
        except AuthError as err:
            return _challenge(err)
        return _jsonrpc_result(
            req_id,
            {
                "tools": [
                    {"name": name, "inputSchema": {"type": "object"}} for name in TOOL_SCOPES
                ]
            },
        )

    if method == "tools/call":
        tool = params.get("name")
        required = TOOL_SCOPES.get(tool)
        if required is None:
            return _jsonrpc_error(req_id, -32601, f"unknown tool: {tool}")
        try:
            claims = validate_bearer(token, required)
        except AuthError as err:
            return _challenge(err)

        args = params.get("arguments") or {}
        if tool == "echo":
            text = args.get("text", "")
            content = [{"type": "text", "text": text}]
        elif tool == "whoami":
            content = [{"type": "text", "text": claims.get("sub", "unknown")}]
        elif tool == "write_note":
            content = [{"type": "text", "text": f"noted: {args.get('note', '')}"}]
        else:  # pragma: no cover
            return _jsonrpc_error(req_id, -32601, f"unknown tool: {tool}")
        return _jsonrpc_result(req_id, {"content": content, "isError": False})

    return _jsonrpc_error(req_id, -32601, f"method not found: {method}")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
