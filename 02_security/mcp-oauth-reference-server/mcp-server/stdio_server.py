"""stdio transport variant of the reference MCP server.

Same tool set as the HTTP server but runs without OAuth, intended for local
testing from assistants that launch MCP servers as subprocesses.
"""
from __future__ import annotations

import json
import sys
from typing import Any

TOOLS = {
    "echo": lambda args: args.get("text", ""),
    "whoami": lambda _args: "local-stdio-user",
    "write_note": lambda args: f"noted: {args.get('note', '')}",
}


def _respond(req_id: Any, result: Any | None = None, error: dict[str, Any] | None = None) -> None:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            _respond(
                req_id,
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mcp-oauth-reference-stdio", "version": "0.1.0"},
                },
            )
        elif method == "tools/list":
            _respond(req_id, {"tools": [{"name": n, "inputSchema": {"type": "object"}} for n in TOOLS]})
        elif method == "tools/call":
            tool = params.get("name")
            fn = TOOLS.get(tool)
            if fn is None:
                _respond(req_id, error={"code": -32601, "message": f"unknown tool: {tool}"})
                continue
            text = fn(params.get("arguments") or {})
            _respond(req_id, {"content": [{"type": "text", "text": text}], "isError": False})
        else:
            _respond(req_id, error={"code": -32601, "message": f"method not found: {method}"})


if __name__ == "__main__":
    main()
