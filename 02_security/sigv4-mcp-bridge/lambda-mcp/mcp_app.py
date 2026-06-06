"""Streamable-HTTP MCP server as a Lambda handler.

Invoked via a Function URL with `AuthType: AWS_IAM`, so the handler only
sees requests that IAM has already verified. The caller's IAM identity is
available on the event under `requestContext.authorizer.iam`.

The handler speaks JSON-RPC 2.0 and implements `initialize`, `tools/list`,
and `tools/call` for three demo tools: `echo`, `whoami`, and
`list_caller_identity`.
"""
from __future__ import annotations

import json
from typing import Any

TOOLS: dict[str, dict[str, Any]] = {
    "echo": {
        "description": "Return the provided text verbatim.",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to echo back."}},
            "required": ["text"],
        },
    },
    "whoami": {
        "description": "Return the IAM principal ARN of the SigV4-authenticated caller.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    "list_caller_identity": {
        "description": "Return the full IAM identity block the Function URL forwarded.",
        "inputSchema": {"type": "object", "properties": {}},
    },
}


def _result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _iam_identity(event: dict[str, Any]) -> dict[str, Any]:
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("iam", {})
    )


def _http_response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "").upper()
    path = event.get("rawPath", "")

    if method != "POST" or not path.endswith("/mcp"):
        return _http_response(404, {"error": "not found"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError as exc:
        return _http_response(400, {"error": f"invalid json: {exc}"})

    rpc_method = body.get("method")
    params = body.get("params") or {}
    req_id = body.get("id")

    if rpc_method == "initialize":
        return _http_response(
            200,
            _result(
                req_id,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "sigv4-mcp-bridge-lambda", "version": "0.1.0"},
                },
            ),
        )

    if rpc_method == "tools/list":
        return _http_response(
            200,
            _result(
                req_id,
                {
                    "tools": [
                        {"name": name, "description": spec["description"], "inputSchema": spec["inputSchema"]}
                        for name, spec in TOOLS.items()
                    ]
                },
            ),
        )

    if rpc_method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            return _http_response(200, _result(req_id, _text(str(args.get("text", "")))))
        if name == "whoami":
            iam = _iam_identity(event)
            return _http_response(
                200,
                _result(req_id, _text(iam.get("userArn") or iam.get("principalId") or "unknown")),
            )
        if name == "list_caller_identity":
            return _http_response(
                200,
                _result(req_id, _text(json.dumps(_iam_identity(event), indent=2))),
            )
        return _http_response(200, _error(req_id, -32601, f"unknown tool: {name}"))

    return _http_response(200, _error(req_id, -32601, f"method not found: {rpc_method}"))
