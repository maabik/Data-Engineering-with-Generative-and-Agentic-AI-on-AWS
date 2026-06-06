"""Reference MCP client: DCR + Authorization Code + PKCE + refresh + tool call.

Run end-to-end after `deploy.sh` and after both the DCR proxy (:8080) and the
MCP server (:8081) are running. The client will:

  1. Fetch authorization-server metadata.
  2. Dynamically register itself (RFC 7591) through the DCR proxy.
  3. Start a loopback HTTP server to receive the authorization code.
  4. Open the browser at Cognito's hosted UI (Authorization Code + PKCE).
  5. Exchange the code for an access + refresh token.
  6. Call an MCP tool with the access token.
  7. Use the refresh token to mint a new access token.
  8. Call another tool with the refreshed token.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass

import httpx

DCR_BASE = os.environ.get("DCR_PROXY_URL", "http://localhost:8080")
MCP_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8081/mcp")
REDIRECT_URI = os.environ.get("CLIENT_REDIRECT_URI", "http://localhost:8765/callback")


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


@dataclass
class AuthServerMetadata:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str
    scopes_supported: list[str]


def discover() -> AuthServerMetadata:
    meta = httpx.get(f"{DCR_BASE}/.well-known/oauth-authorization-server").raise_for_status().json()
    reg = meta["registration_endpoint"]
    if reg.startswith("/"):
        reg = f"{DCR_BASE}{reg}"
    return AuthServerMetadata(
        authorization_endpoint=meta["authorization_endpoint"],
        token_endpoint=meta["token_endpoint"],
        registration_endpoint=reg,
        scopes_supported=meta["scopes_supported"],
    )


def register(meta: AuthServerMetadata) -> dict:
    resp = httpx.post(
        meta.registration_endpoint,
        json={
            "client_name": "mcp-oauth-reference-client",
            "redirect_uris": [REDIRECT_URI],
            "grant_types": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "none",
            "scope": " ".join(meta.scopes_supported),
        },
    )
    resp.raise_for_status()
    return resp.json()


def _capture_code(redirect_uri: str) -> str:
    parsed = urllib.parse.urlparse(redirect_uri)
    received: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            qs = urllib.parse.urlparse(self.path).query
            received.update(dict(urllib.parse.parse_qsl(qs)))
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"You can close this tab.")

        def log_message(self, *_a, **_k):  # noqa: D401
            pass

    server = http.server.HTTPServer((parsed.hostname, parsed.port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    while "code" not in received and "error" not in received:
        time.sleep(0.05)
    server.shutdown()
    if "error" in received:
        raise RuntimeError(f"authorization failed: {received}")
    return received["code"]


def authorize(meta: AuthServerMetadata, client_id: str, scopes: list[str]) -> tuple[str, str]:
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{meta.authorization_endpoint}?{urllib.parse.urlencode(params)}"
    print("Opening browser for consent:", url)
    webbrowser.open(url)
    code = _capture_code(REDIRECT_URI)
    return code, verifier


def exchange_code(meta: AuthServerMetadata, client_id: str, code: str, verifier: str) -> dict:
    resp = httpx.post(
        meta.token_endpoint,
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()


def refresh(meta: AuthServerMetadata, client_id: str, refresh_token: str) -> dict:
    resp = httpx.post(
        meta.token_endpoint,
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()


def call_tool(access_token: str, name: str, arguments: dict) -> dict:
    resp = httpx.post(
        MCP_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    meta = discover()
    registration = register(meta)
    client_id = registration["client_id"]
    print(f"Registered client_id={client_id}")

    code, verifier = authorize(meta, client_id, meta.scopes_supported)
    tokens = exchange_code(meta, client_id, code, verifier)
    print("Got access token (truncated):", tokens["access_token"][:24], "…")

    print("Calling echo:", json.dumps(call_tool(tokens["access_token"], "echo", {"text": "hello"}), indent=2))

    refreshed = refresh(meta, client_id, tokens["refresh_token"])
    print("Refreshed access token (truncated):", refreshed["access_token"][:24], "…")
    print("Calling whoami:", json.dumps(call_tool(refreshed["access_token"], "whoami", {}), indent=2))


if __name__ == "__main__":
    main()
