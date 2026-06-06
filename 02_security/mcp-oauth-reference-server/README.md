# MCP OAuth 2.1 + PKCE Reference Server

A clean, reusable reference implementation of the OAuth 2.1 + PKCE authorization
flow for Model Context Protocol (MCP) servers, backed by Amazon Cognito.
Chapter 2 of *Data Engineering with Generative and Agentic AI on AWS* walks
through the protocol in the abstract; this project implements it so you can
run it, break it, and fork it.

Use it as a starter when you need to add OAuth to any MCP server: swap in your
own tools and keep the auth layer as-is.

---

## Table of contents

1. [What problem this solves](#what-problem-this-solves)
2. [Architecture](#architecture)
3. [End-to-end OAuth flow](#end-to-end-oauth-flow)
4. [Repo layout](#repo-layout)
5. [Component deep dives](#component-deep-dives)
   - [`infra/cognito.yaml`](#infracognitoyaml)
   - [`dcr-proxy/`](#dcr-proxy)
   - [`mcp-server/`](#mcp-server)
   - [`mcp-client/`](#mcp-client)
   - [`scripts/`](#scripts)
6. [Quickstart](#quickstart)
7. [Verifying each piece by hand](#verifying-each-piece-by-hand)
8. [Extending it](#extending-it)
9. [Security caveats and production hardening](#security-caveats-and-production-hardening)
10. [Troubleshooting](#troubleshooting)
11. [Teardown](#teardown)

---

## What problem this solves

The MCP specification classifies MCP servers as OAuth 2.1 resource servers and
expects three things from the authorization server:

- **Dynamic Client Registration (RFC 7591)** — agents can meet unfamiliar MCP
  servers at runtime, so they can't be pre-registered.
- **PKCE (RFC 7636)** — public clients (CLIs, desktop agents) can't hold a
  client secret, so PKCE replaces it.
- **Authorization Server Metadata (RFC 8414) and Protected Resource Metadata
  (RFC 9728)** — discovery documents that let a client figure out where to
  register, where to send the user for consent, and where to get tokens.

Amazon Cognito is a solid authorization server but doesn't implement RFC 7591
natively. This project closes that gap with a tiny FastAPI proxy, then wires
everything together end-to-end so you have a working, inspectable reference.

---

## Architecture

```
                 ┌──────────────────────────┐
                 │   Cognito User Pool      │
                 │   (authorization server) │
                 └─────────▲────────────────┘
                           │ AdminCreateUserPoolClient
                           │ (bridge to RFC 7591)
┌────────────┐   /register ┌──────────────┐   authorize / token
│ MCP Client │────────────▶│  DCR Proxy   │──────────────────────────┐
│ (+ PKCE)   │             │  (FastAPI)   │                          ▼
└──────┬─────┘             └──────────────┘                 ┌────────────────┐
       │                                                    │ Cognito Hosted │
       │       Bearer <access_token>                        │   UI / OAuth   │
       │   ┌───────────────────────────────┐                └────────────────┘
       └──▶│  MCP Server (streamable HTTP) │
           │  JWT verify · scope check     │
           └───────────────────────────────┘
```

Three processes, one AWS stack:

| Piece | Runtime | Responsibility |
|------|---------|----------------|
| Cognito user pool | AWS | Authorization server, JWKS, hosted UI, issues access + refresh tokens |
| DCR proxy | local (FastAPI) | `/register` + `/.well-known/*` metadata |
| MCP server | local (FastAPI) | Resource server; validates JWTs, enforces per-tool scopes |
| MCP client | local (CLI) | Orchestrates discovery → DCR → PKCE → tool call → refresh |

---

## End-to-end OAuth flow

This is what happens when you run `python mcp-client/client.py`:

```
┌────────┐       ┌────────────┐      ┌─────────────┐     ┌──────────────┐     ┌────────────┐
│ Client │       │ DCR Proxy  │      │   Cognito   │     │  User/Browser │     │ MCP Server │
└───┬────┘       └──────┬─────┘      └──────┬──────┘     └──────┬───────┘     └──────┬─────┘
    │ 1. GET /.well-known/oauth-authorization-server            │                    │
    ├───────────────────▶                                       │                    │
    │◀─ authorization_endpoint, token_endpoint, registration_endpoint, scopes        │
    │                                                           │                    │
    │ 2. POST /register  (RFC 7591)                             │                    │
    ├───────────────────▶                                       │                    │
    │                    │ AdminCreateUserPoolClient            │                    │
    │                    ├────────────────────▶                 │                    │
    │◀── client_id (public client, no secret) ─────────────────                       │
    │                                                           │                    │
    │ 3. Generate code_verifier + code_challenge (SHA-256)      │                    │
    │ 4. Open browser → /oauth2/authorize?code_challenge=…      │                    │
    ├────────────────────────────────────────────────────────────▶                   │
    │                                                      Sign in with email+pw     │
    │                                                           │                    │
    │ 5. Cognito 302 → http://localhost:8765/callback?code=…    │                    │
    │◀──────────────── code captured by loopback server ────────                     │
    │                                                           │                    │
    │ 6. POST /oauth2/token  (code + code_verifier)             │                    │
    ├──────────────────────────────────────▶                    │                    │
    │◀── access_token (JWT, 1h), refresh_token (30d), id_token  │                    │
    │                                                           │                    │
    │ 7. POST /mcp  Authorization: Bearer <access_token>        │                    │
    │       { "method": "tools/call", "params": {"name":"echo", …} }                 │
    ├───────────────────────────────────────────────────────────────────────────────▶│
    │                                                           │   verify JWT via   │
    │                                                           │   JWKS, check scope│
    │◀── { "result": { "content": [{ "text": "hi" }] } }                             │
    │                                                           │                    │
    │ 8. POST /oauth2/token  (grant_type=refresh_token)         │                    │
    ├──────────────────────────────────────▶                    │                    │
    │◀── new access_token                                       │                    │
    │                                                           │                    │
    │ 9. POST /mcp with refreshed token → tool succeeds again                        │
    └────────────────────────────────────────────────────────────────────────────────┘
```

Key details:

- **Step 2** is MCP-specific: the client doesn't know about Cognito
  `AdminCreateUserPoolClient`; it speaks RFC 7591 and the DCR proxy translates.
- **Step 3**: `code_verifier` is 32 random bytes (base64url). `code_challenge`
  is `BASE64URL(SHA256(code_verifier))`. Cognito stores the challenge with the
  authorization code and requires the matching verifier at step 6.
- **Step 5**: state is echoed back and the client must verify it (the reference
  client generates but doesn't re-verify state — see
  [production hardening](#security-caveats-and-production-hardening)).
- **Step 7**: the MCP server fetches Cognito's JWKS, caches it for an hour, and
  verifies the JWT's `kid`, signature, `iss`, `token_use=access`, and the
  presence of the scope required by the requested tool.

---

## Repo layout

```
mcp-oauth-reference-server/
├── README.md                    you are here
├── .env.example                 shape of the env file deploy.sh writes
├── .gitignore                   .env, .venv, __pycache__
├── infra/
│   └── cognito.yaml             user pool + domain + resource server + IAM role
├── dcr-proxy/
│   ├── app.py                   /register, /.well-known/*
│   └── requirements.txt
├── mcp-server/
│   ├── app.py                   streamable-HTTP MCP resource server
│   ├── auth.py                  JWKS fetch + JWT validation + scope check
│   ├── stdio_server.py          stdio transport variant (no OAuth)
│   └── requirements.txt
├── mcp-client/
│   ├── client.py                reference client: DCR + PKCE + refresh + tool
│   └── requirements.txt
└── scripts/
    ├── deploy.sh                CloudFormation deploy + writes .env
    └── teardown.sh              CloudFormation delete
```

---

## Component deep dives

### `infra/cognito.yaml`

One CloudFormation template. Creates:

- **`UserPool`** — password policy (12+ chars, mixed case, digit, symbol),
  email as the sign-in alias, admin-create-only (self-signup disabled).
- **`UserPoolDomain`** — Cognito hosted UI domain. `DomainPrefix` must be
  globally unique.
- **`ResourceServer`** — declares the scopes your MCP server enforces.
  Identifier is `https://<StackPrefix>.mcp`; scopes are:
  - `tools.read` — list and call read-only tools
  - `tools.write` — call mutating tools
  Fully-qualified scope names (what clients and JWTs use) are
  `https://<StackPrefix>.mcp/tools.read` and `.../tools.write`.
- **`BootstrapClient`** — a pre-registered Cognito app client, useful if you
  want to bypass DCR for CI or a known agent. The reference client doesn't use
  it; it registers dynamically.
- **`DcrProxyRole`** — IAM role the DCR proxy would assume when running on
  Lambda or EC2. The trust policy allows `lambda.amazonaws.com` and
  `ec2.amazonaws.com`; the policy grants `cognito-idp:Create/Describe/Update/
  DeleteUserPoolClient` scoped to this user pool's ARN. Running the proxy
  locally uses your user's credentials instead of this role.

Outputs are consumed by `scripts/deploy.sh` and written into `.env`:
`UserPoolId`, `Issuer`, `HostedUiDomain`, `AuthorizationEndpoint`,
`TokenEndpoint`, `ResourceServerIdentifier`, `BootstrapClientId`,
`DcrProxyRoleArn`.

### `dcr-proxy/`

`dcr-proxy/app.py` — a ~100-line FastAPI service with three endpoints.

**`GET /.well-known/oauth-authorization-server`** — RFC 8414 metadata. Returns
Cognito's endpoints (`/oauth2/authorize`, `/oauth2/token`) alongside
`registration_endpoint: /register` (pointing at this proxy) and the list of
supported scopes. This is the single document a spec-compliant MCP client
fetches first.

**`GET /.well-known/oauth-protected-resource`** — RFC 9728 metadata. The MCP
server's 401 responses point here via `WWW-Authenticate: …
resource_metadata=…`, letting clients discover the issuer and supported scopes.

**`POST /register`** — RFC 7591 Dynamic Client Registration. Accepts the
spec-shaped request:

```json
{
  "client_name": "my-agent",
  "redirect_uris": ["http://localhost:8765/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "token_endpoint_auth_method": "none",
  "scope": "openid email https://mcp-oauth-ref.mcp/tools.read …"
}
```

Under the hood it calls `cognito-idp:CreateUserPoolClient`. If
`token_endpoint_auth_method == "none"` it creates a public client (no secret)
suitable for PKCE; any other value creates a confidential client and returns
`client_secret` in the response. Returns HTTP 201 with `client_id`,
`registration_access_token`, and `registration_client_uri`.

**`DELETE /register/{client_id}`** — removes a previously registered client
from Cognito. Used by the test harness and by clients that want to clean up
after themselves.

Environment variables: `COGNITO_USER_POOL_ID`, `AWS_REGION`,
`COGNITO_HOSTED_DOMAIN`, `MCP_RESOURCE_SERVER_IDENTIFIER`. All are set by
`scripts/deploy.sh` into `.env`.

### `mcp-server/`

Two transports for the same three tools.

**`mcp-server/app.py`** — streamable-HTTP transport. One endpoint `POST /mcp`
that speaks JSON-RPC 2.0. Three MCP methods:

- `initialize` — public (no token). Returns protocol version and
  `serverInfo`. Must be callable before auth so clients can discover what's
  there.
- `tools/list` — requires `tools.read`. Returns the tool catalog.
- `tools/call` — required scope depends on the tool (see `TOOL_SCOPES`):
  `echo` and `whoami` need `tools.read`; `write_note` needs `tools.write`.

On any auth failure the server returns a 401/403 with an RFC 9728-compliant
`WWW-Authenticate: Bearer error="…", resource_metadata="…"` header so clients
know where to fetch metadata and restart the flow.

**`mcp-server/auth.py`** — the entire JWT-validation layer:

- Fetch `<issuer>/.well-known/jwks.json` (cached 1 hour).
- Look up the signing key by `kid`.
- Call `jose.jwt.decode(token, key, algorithms=[alg], issuer=ISSUER,
  options={"verify_aud": False})`. Cognito access tokens omit `aud`, so
  audience verification is disabled by design; the `iss` check is what protects
  you from tokens issued by other pools.
- Require `token_use == "access"` to reject ID tokens.
- Require the fully-qualified scope in the `scope` claim.

Any failure raises `AuthError(code, description, status)` which the HTTP
handler turns into a challenge response.

**`mcp-server/stdio_server.py`** — stdio transport with the same three tools
but no OAuth. Useful for local testing from assistants that launch MCP servers
as subprocesses (e.g. Claude Desktop, Kiro). The body is intentionally tiny —
read stdin line-by-line, dispatch, write to stdout.

### `mcp-client/`

`mcp-client/client.py` — a ~170-line reference client that exercises the full
flow. Each function maps to one step in the sequence diagram above:

- `discover()` — `GET /.well-known/oauth-authorization-server`.
- `register(meta)` — `POST /register` via the DCR proxy.
- `_pkce_pair()` — 32-byte random verifier, SHA-256 challenge.
- `authorize(meta, client_id, scopes)` — opens the system browser at Cognito's
  `/oauth2/authorize`, spins up an `http.server` on port 8765 to receive the
  redirect, captures `?code=…`, returns `(code, verifier)`.
- `exchange_code(…)` — `POST /oauth2/token` with `grant_type=authorization_code`
  and `code_verifier`. Returns the token response.
- `refresh(…)` — `POST /oauth2/token` with `grant_type=refresh_token`.
- `call_tool(access_token, name, args)` — `POST /mcp` with the bearer token.

Env vars: `DCR_PROXY_URL` (default `http://localhost:8080`), `MCP_SERVER_URL`
(default `http://localhost:8081/mcp`), `CLIENT_REDIRECT_URI` (default
`http://localhost:8765/callback`).

### `scripts/`

- **`deploy.sh`** — `aws cloudformation deploy` with a random 8-char
  `DomainPrefix`, then reads the stack outputs and writes `.env` in the project
  root. Idempotent — re-running updates the stack in place. Region comes from
  `AWS_REGION` env var or `aws configure get region`.
- **`teardown.sh`** — `aws cloudformation delete-stack` and waits for
  completion. Safe to run even if nothing was deployed (fails cleanly).

---

## Quickstart

```bash
# From mcp-oauth-reference-server/

# 1. Deploy Cognito
./scripts/deploy.sh

# 2. Load stack outputs into your shell
source .env

# 3. Install deps
python3 -m venv .venv && source .venv/bin/activate
pip install -r dcr-proxy/requirements.txt \
            -r mcp-server/requirements.txt \
            -r mcp-client/requirements.txt

# 4. Start the DCR proxy (terminal 1)
uvicorn app:app --port 8080 --app-dir dcr-proxy

# 5. Start the MCP server (terminal 2, also `source .env` first)
uvicorn app:app --port 8081 --app-dir mcp-server

# 6. Create a test user (once, avoids email verification)
aws cognito-idp admin-create-user \
  --user-pool-id "$COGNITO_USER_POOL_ID" \
  --username test@example.com \
  --user-attributes Name=email,Value=test@example.com Name=email_verified,Value=true \
  --message-action SUPPRESS
aws cognito-idp admin-set-user-password \
  --user-pool-id "$COGNITO_USER_POOL_ID" \
  --username test@example.com \
  --password 'SomeStrongPw!123' --permanent

# 7. Run the client (terminal 3, also `source .env`)
python mcp-client/client.py
```

The client opens your default browser, you sign in, the redirect fires,
you see `Got access token…`, two tool results, and a refreshed token.

---

## Verifying each piece by hand

Useful when something's off or you want to see the raw wire format.

**Discovery metadata**
```bash
curl -s http://localhost:8080/.well-known/oauth-authorization-server | jq
curl -s http://localhost:8080/.well-known/oauth-protected-resource | jq
```

**Dynamic Client Registration round-trip**
```bash
curl -s -X POST http://localhost:8080/register \
  -H 'Content-Type: application/json' \
  -d '{"client_name":"byhand","redirect_uris":["http://localhost:8765/callback"],"token_endpoint_auth_method":"none"}' | jq

# Delete the client you just created
curl -s -X DELETE http://localhost:8080/register/<client_id>
```

**MCP server rejects unauthenticated tool calls**
```bash
curl -si -X POST http://localhost:8081/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"echo","arguments":{"text":"hi"}}}'
# → 401, WWW-Authenticate: Bearer error="invalid_request", …
```

**MCP server allows unauthenticated `initialize`**
```bash
curl -s -X POST http://localhost:8081/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}'
```

**Inspect a decoded access token** — paste the JWT into
`https://jwt.io` or `python -c 'import jwt; print(jwt.decode(t, options={"verify_signature": False}))'`
and confirm `iss`, `token_use=access`, and the `scope` claim.

---

## Extending it

**Add a new MCP tool**

1. Pick a scope. If the tool is read-only use `tools.read`; for mutating ops
   add a new scope to `infra/cognito.yaml`'s `ResourceServer` and redeploy.
2. Add an entry to `TOOL_SCOPES` in `mcp-server/app.py` mapping tool name →
   required scope.
3. Add a branch under `if method == "tools/call":` returning MCP content.
4. If you added a scope, update `DEFAULT_SCOPES` in `dcr-proxy/app.py` so
   discovery advertises it.

**Swap to a different IdP** (Keycloak, Auth0, Okta)

Those IdPs implement RFC 7591 natively — you can delete the DCR proxy entirely
and point `mcp-client/client.py`'s `DCR_PROXY_URL` at the IdP directly. Update
`COGNITO_ISSUER` in `mcp-server/auth.py` to the new issuer; JWKS validation is
standards-based and will still work.

**Deploy to AWS instead of localhost**

- DCR proxy → Lambda + Function URL (or API Gateway). Use `DcrProxyRoleArn`
  from the stack as the Lambda execution role. Remove `--app-dir` and package
  `app.py` as the handler with
  [Mangum](https://github.com/jordaneremieff/mangum).
- MCP server → Lambda + API Gateway, Fargate, or AgentCore Runtime. See the
  `12_agentic/mcp/` folder in this repo for AgentCore deployment patterns.
- Flip `MCP_PROTECTED_RESOURCE_METADATA_URL` to the public URL of the DCR
  proxy.

**Add token introspection (RFC 7662)**

Cognito doesn't expose introspection, so the MCP server uses local JWT
validation instead. If you need introspection for opaque tokens or to check
revocation status, add a `/introspect` endpoint to the DCR proxy that calls
`cognito-idp:GetUser` with the access token and returns the standards-shaped
response (`{"active": true, "scope": …, "sub": …, "exp": …}`).

---

## Security caveats and production hardening

This is a reference. Before production:

- **Protect `/register`.** RFC 7591 says DCR should be open; real deployments
  typically gate it with a `registration_access_token` issued out-of-band, or
  a mutual-TLS client cert, or by requiring an initial bootstrap token. The
  proxy returns a `registration_access_token` but doesn't validate it on
  subsequent management requests — add that check, or put the proxy behind an
  API Gateway that requires an API key, or front it with IAM auth via SigV4.
- **Verify `state`.** The reference client generates `state` but doesn't
  re-verify the value returned on the callback. In production you must compare
  the incoming `state` against the one you generated to prevent CSRF.
- **Pin redirect URIs.** The proxy accepts any redirect URI the registration
  request provides. In production, restrict to an allowlist or require HTTPS
  redirect URIs except for `localhost`.
- **Rotate refresh tokens.** Cognito supports refresh token rotation; enable
  it on the app client for defense against refresh-token theft.
- **Cap the JWKS cache.** The current 1-hour cache is fine for low-volume use.
  For high traffic, prefetch on startup and refresh in a background task.
- **Audit everything.** Turn on CloudTrail data events for the user pool; log
  every `/register` call and every MCP tool invocation with the JWT `sub`.
- **Scope audience.** Cognito omits `aud` from access tokens. If you need to
  distinguish which resource server a token was minted for (e.g. multiple MCP
  servers sharing a pool), rely on the `scope` claim's resource-server-prefixed
  scopes (e.g. `https://mcp-a.example/tools.read` vs
  `https://mcp-b.example/tools.read`) — that's exactly how Cognito's scope
  naming scheme is designed to work.

---

## Troubleshooting

**`deploy.sh` exits silently with no stack created.**
The domain-prefix generator pipes `/dev/urandom → tr → head`. Under
`set -o pipefail`, `head -c` closing stdin before `tr` finishes can kill the
pipeline with SIGPIPE. The script is patched; if you wrote your own variant,
use `head -c 128 /dev/urandom | tr … | cut -c1-8` instead of `tr … | head`.

**`InvalidParameterException: Domain already associated with another user pool`.**
Cognito hosted-UI domains are globally unique. Re-run `deploy.sh` — it
generates a new random prefix each time. Or set `DOMAIN_PREFIX` manually.

**`401 invalid_token: unknown key id`.**
JWKS cache is stale or the token was issued by a different pool. Restart the
MCP server (which purges the `lru_cache`) or verify the token's `iss` matches
`COGNITO_ISSUER`.

**Client hangs after the browser redirect.**
Check that `CLIENT_REDIRECT_URI` is `http://localhost:8765/callback` (with the
trailing `/callback`), that it's listed under `CallbackURLs` on the Cognito
client (DCR sets this automatically), and that nothing else is bound to 8765.

**`invalid_grant` on token exchange.**
Code expired (they're single-use and short-lived) or the `code_verifier`
doesn't match the earlier `code_challenge`. Re-run the client from scratch.

**`insufficient_scope`.**
The user signed in but the app client wasn't granted the scope the tool
requires. Check the app client's `AllowedOAuthScopes` in Cognito matches what
the DCR proxy advertised — mismatches happen when you add a scope to the
resource server but forget to update `DEFAULT_SCOPES` in `dcr-proxy/app.py`.

**Browser opens but the hosted UI shows an error.**
The most common cause is the `scope` query param containing a scope the app
client isn't authorized for. Inspect the URL the client printed and confirm
each scope exists on the resource server and is listed on the client.

---

## Teardown

```bash
./scripts/teardown.sh
rm -f .env
rm -rf .venv
```

`teardown.sh` deletes the CloudFormation stack, which removes the user pool,
hosted domain, resource server, all app clients (including those created via
DCR), and the IAM role. Cost during testing is effectively zero (Cognito's
free tier covers 50k MAUs; this project touches one).
