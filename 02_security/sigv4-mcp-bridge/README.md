# SigV4 → MCP Bridge for Strands Agents

Drop-in Python middleware that lets any [Strands](https://strandsagents.com/)
agent authenticate to MCP servers via AWS Signature Version 4 instead of
OAuth. Useful when both the agent and the MCP server run inside AWS — the
signing credentials are whatever role the agent's compute uses, so there's no
token dance, no IdP, no browser.

Chapter 2's "IAM Authentication with SigV4 for MCP" sidebar references AWS's
[`mcp-proxy-for-aws`](https://github.com/aws/mcp-proxy-for-aws) package. This
project wraps that package with a Strands-specific factory, adds a deployable
Lambda-hosted MCP server for realistic testing, and documents the full path
end-to-end — signing, IAM policy shape, Strands integration, and Bedrock
model-ID gotchas.

Tested end-to-end against live AWS: Lambda Function URL with `AuthType:
AWS_IAM`, SigV4-signed MCP traffic, Bedrock-backed Strands agent calling
tools. See [Test notes](#test-notes-from-a-real-run) for the exact command
trail.

---

## Table of contents

1. [When to use SigV4 vs OAuth](#when-to-use-sigv4-vs-oauth)
2. [Architecture](#architecture)
3. [How the signing works](#how-the-signing-works)
4. [Repo layout](#repo-layout)
5. [Component deep dives](#component-deep-dives)
   - [`sigv4_mcp_bridge/`](#sigv4_mcp_bridge)
   - [`lambda-mcp/`](#lambda-mcp)
   - [`infra/bridge.yaml`](#infrabridgeyaml)
   - [`agent/`](#agent)
   - [`scripts/`](#scripts)
6. [Quickstart](#quickstart)
7. [Using the bridge in your own agent](#using-the-bridge-in-your-own-agent)
8. [Verifying by hand](#verifying-by-hand)
9. [Security caveats](#security-caveats)
10. [Troubleshooting](#troubleshooting)
11. [Test notes from a real run](#test-notes-from-a-real-run)
12. [Teardown](#teardown)

---

## When to use SigV4 vs OAuth

The `mcp-oauth-reference-server/` sibling project solves the public MCP
case — agents, users, or third parties that live outside AWS and need a
human-in-the-loop consent screen. SigV4 solves the opposite case:

| | OAuth 2.1 + PKCE | SigV4 |
|---|---|---|
| Caller identity | End user (human-in-the-loop) or a registered client | AWS IAM principal (role or user) |
| Consent UI | Required | None |
| Token lifetime | Short access + refresh | Per-request signature, no tokens |
| Where it shines | Public MCP servers, multi-tenant SaaS agents | Internal AWS→AWS traffic: Strands agents on Lambda / Fargate / EC2 / AgentCore calling an MCP server that also lives in AWS |
| What the client sends | `Authorization: Bearer <jwt>` | `Authorization: AWS4-HMAC-SHA256 Credential=…, SignedHeaders=…, Signature=…` + `X-Amz-Date`, `X-Amz-Security-Token` |
| Who enforces auth | MCP server's JWT middleware | AWS (Function URL / API Gateway) — handler never sees an unsigned request |

Use SigV4 when the agent's caller identity is already an IAM principal and
you don't want to manage a separate IdP.

---

## Architecture

```
┌──────────────────────────────┐
│ Strands agent                │
│   model: us.anthropic.claude-│
│     haiku-4-5-…              │
│   tools: MCPClient(factory)  │◀── sigv4_mcp_bridge.strands_mcp_client(...)
└──────────────┬───────────────┘
               │ streamable HTTP
               │ Authorization: AWS4-HMAC-SHA256 …
               │ X-Amz-Date: 20260512T114031Z
               │ X-Amz-Security-Token: <sts session token>
               ▼
┌──────────────────────────────┐
│ Lambda Function URL          │
│ AuthType: AWS_IAM            │◀── IAM verifies signature BEFORE
│ InvokeMode: BUFFERED         │    the handler runs
│ runtime: python3.12 (arm64)  │
│ handler: mcp_app.handler     │
└──────────────────────────────┘
               │ JSON-RPC 2.0 over HTTP
               ▼
      MCP tools: echo, whoami, list_caller_identity
```

Two processes, one AWS stack:

| Piece | Runtime | Responsibility |
|------|---------|----------------|
| Lambda MCP target | AWS Lambda + Function URL (IAM-auth) | Runs the MCP server; auth is enforced by the Function URL's IAM check before the handler runs |
| Bridge middleware | In-process with the agent | Wraps the MCP client's HTTP transport so every request is SigV4-signed with the agent's AWS credentials |

The Lambda handler never sees a raw request that wasn't signed — the Function
URL's `AuthType: AWS_IAM` rejects anything without a valid SigV4 signature
before invoking the handler. Observed 403 response for unsigned curl:

```
HTTP/1.1 403 Forbidden
x-amzn-ErrorType: AccessDeniedException
{"Message":"Forbidden"}
```

---

## How the signing works

SigV4 signs a canonical representation of the request (method + URL + headers
+ payload hash) with your secret key using HMAC-SHA256. The result goes in
the `Authorization` header alongside `X-Amz-Date`, `X-Amz-Security-Token`
(for STS sessions), and optional `X-Amz-Content-Sha256`.

```
client                                            Lambda Function URL (AWS_IAM)
  │                                                          │
  │ 1. MCP client builds POST /mcp JSON body                 │
  │                                                          │
  │ 2. sigv4_mcp_bridge constructs the transport via         │
  │    mcp_proxy_for_aws.aws_iam_streamablehttp_client.       │
  │    botocore.auth.SigV4Auth.add_auth(request) signs it    │
  │    with the boto3 credential chain's current creds       │
  │                                                          │
  │ 3. Authorization: AWS4-HMAC-SHA256                       │
  │    Credential=ASIA.../20260512/us-west-2/lambda/aws4_request,
  │    SignedHeaders=host;x-amz-date;x-amz-security-token,   │
  │    Signature=abcd…                                       │
  ├──────────────────────────────────────────────────────────▶
  │                                                          │
  │                                  IAM recomputes canonical│
  │                                  request, verifies sig,  │
  │                                  checks caller has       │
  │                                  lambda:InvokeFunctionUrl│
  │                                                          │
  │◀── 200 OK { "jsonrpc": "2.0", "result": … } ─────────────│
```

Two things worth pinning down:

- **Service name** for a Function URL is `lambda` (not `apigateway`). Region
  is wherever the function lives. Both get baked into the signing key
  derivation path (`AWS4-HMAC-SHA256/<date>/<region>/<service>/aws4_request`).
  Wrong service or region → `SignatureDoesNotMatch`.
- **Payload hash** is computed inline for JSON-RPC bodies (they're small).
  Streamed S3-style bodies use `UNSIGNED-PAYLOAD`; `aws/mcp-proxy-for-aws`
  doesn't need that for MCP traffic.

The caller ARN that reaches the Lambda handler via
`event['requestContext']['authorizer']['iam']` is the actual assumed-role
session, not the role itself — e.g.
`arn:aws:sts::123456789012:assumed-role/Admin/example-user`.

---

## Repo layout

```
sigv4-mcp-bridge/
├── README.md                       you are here
├── .env.example                    shape of the env file deploy.sh writes
├── .gitignore                      .env, .venv, build/, __pycache__, *.zip
├── sigv4_mcp_bridge/
│   ├── __init__.py                 strands_mcp_client(...) factory
│   └── requirements.txt
├── lambda-mcp/
│   ├── mcp_app.py                  Lambda handler: streamable-HTTP MCP server
│   └── requirements.txt            (empty — stdlib-only)
├── infra/
│   └── bridge.yaml                 Lambda + Function URL (IAM-auth) + caller role
├── agent/
│   ├── agent.py                    Strands agent on Claude Haiku 4.5 via Bedrock
│   ├── smoke_test.py               Raw MCPClient round-trip (no Bedrock)
│   └── requirements.txt
└── scripts/
    ├── deploy.sh                   CFN deploy + package Lambda + write .env
    └── teardown.sh                 CFN delete + optional bucket purge
```

---

## Component deep dives

### `sigv4_mcp_bridge/`

A ~25-line package. The public surface is one factory:

```python
from sigv4_mcp_bridge import strands_mcp_client

mcp = strands_mcp_client(
    url="https://abc123xyz.lambda-url.us-west-2.on.aws/mcp",
    service="lambda",
    region="us-west-2",
)
```

Under the hood it builds a `_factory()` closure that calls
`mcp_proxy_for_aws.client.aws_iam_streamablehttp_client(endpoint=..., aws_service=..., aws_region=...)`
and hands the resulting transport to `strands.tools.mcp.MCPClient`.

**Why a factory, not a client?** Strands's `MCPClient` accepts a *callable*
that returns a fresh transport each time. This lets Strands open and close
the transport cleanly around the agent's lifecycle, and it makes credential
refresh work naturally — each call resolves credentials through the boto3
chain, so rotating STS sessions just work without restarting the agent.

**Optional kwargs**:
- `profile=` — AWS profile name, if you're not using the default.
- `credentials=` — a `botocore.credentials.Credentials` object, useful when
  you've already done the STS assume-role yourself and want to pin the
  credentials explicitly.

The factory signature intentionally does *not* expose `timeout` or
`httpx_client_factory` — those are `aws_iam_streamablehttp_client`'s advanced
knobs, easy to pass through when you need them. Patch `__init__.py` if you
do.

### `lambda-mcp/`

`lambda-mcp/mcp_app.py` is a tiny streamable-HTTP MCP server implemented as
a pure Lambda handler. No FastAPI, no Mangum — the handler parses the
Function URL v2 event envelope itself to keep cold starts under 200 ms and
the deployment zip under 2 KB.

**Tools and schemas** (this is what `tools/list` returns, verbatim):

| Tool | Input schema | Output |
|---|---|---|
| `echo` | `{type: object, properties: {text: string}, required: [text]}` | Echoes `text`. |
| `whoami` | `{type: object, properties: {}}` | The IAM `userArn` from the Function URL's request context. |
| `list_caller_identity` | `{type: object, properties: {}}` | Full `requestContext.authorizer.iam` block (account, caller ARN, principalId, userId). |

**Why proper schemas matter.** An early version of this project used
`{"type": "object"}` with no `properties` on every tool. The MCP transport
accepted it, but Claude couldn't figure out that `echo` took a `text`
argument and called it with no input. If you add tools, give them full
schemas with descriptions — it's not cosmetic; the model relies on them.

**What the handler implements** (JSON-RPC 2.0):
- `initialize` → capabilities + `serverInfo`.
- `tools/list` → full tool catalog with descriptions and input schemas.
- `tools/call` → dispatches by tool name.
- Any other `method` → JSON-RPC error `-32601` (method not found).
- Non-`/mcp` path or non-`POST` → HTTP 404.

**What it doesn't implement.** Authentication. That's the Function URL's
job. By the time the handler runs, IAM has already verified the signature
and written the caller's identity onto
`event['requestContext']['authorizer']['iam']`.

### `infra/bridge.yaml`

SAM template (`Transform: AWS::Serverless-2016-10-31`). Creates:

- **`McpFunction`** (`AWS::Serverless::Function`) — Python 3.12 on arm64
  (cheaper and faster cold start), 512 MB, 30 s timeout. Code points at a
  zip in the staging S3 bucket.
- **`McpFunctionUrl`** (`AWS::Lambda::Url`) — `AuthType: AWS_IAM`,
  `InvokeMode: BUFFERED`. Output: `FunctionUrl`.
- **`McpFunctionUrlPermission`** — account-scoped permission that lets
  principals in the current account invoke the Function URL when they
  authenticate with IAM. This is the *resource policy* on the Function URL,
  separate from the caller's *identity policy*.
- **`CallerRole`** — the role a Strands agent would assume (or use as its
  Lambda/EC2/Fargate execution role). Trust policy allows `sts:AssumeRole`
  from the current account root so you can assume it from the CLI for local
  testing. Inline policy grants `lambda:InvokeFunctionUrl` on the exact
  function ARN, gated by a `StringEquals` condition on
  `lambda:FunctionUrlAuthType == AWS_IAM` — belt-and-suspenders against
  accidentally using this role on a non-IAM-auth URL.

Outputs: `FunctionUrl`, `FunctionArn`, `CallerRoleArn`, `Region`. All get
written into `.env` by `deploy.sh`.

**What the stack does *not* do**: give the `McpFunction` any AWS SDK
permissions beyond basic CloudWatch logging. If your MCP tools need to call
AWS services (Redshift Data API, Athena, DynamoDB, etc.), extend the
function's execution role accordingly.

### `agent/`

Two entry points:

- **`agent/agent.py`** — full Strands agent using Claude Haiku 4.5 on
  Bedrock. Loads MCP tools via the bridge and responds to a prompt.
  **Important**: uses the inference-profile model ID
  `us.anthropic.claude-haiku-4-5-20251001-v1:0` (the `us.` prefix routes
  through a cross-region inference profile). The bare model ID
  `anthropic.claude-haiku-4-5-20251001-v1:0` fails with
  `ValidationException: Invocation … with on-demand throughput isn't
  supported` because Haiku 4.5 only supports inference profiles.
- **`agent/smoke_test.py`** — skips the LLM and directly exercises the MCP
  transport: `list_tools_sync()` then `call_tool_sync(tool_use_id=..., name="whoami", arguments={})`.
  Useful for verifying the SigV4 path works without needing Bedrock access.

Both scripts expect `MCP_FUNCTION_URL` and `MCP_REGION` (or `AWS_REGION`) in
the env — `deploy.sh` writes them into `.env`.

**Why `PYTHONPATH=.`?** The bridge package lives at the project root, not
inside `agent/`. Run the scripts from `sigv4-mcp-bridge/` with
`PYTHONPATH=.` so Python can import `sigv4_mcp_bridge`. Alternatively,
`pip install -e .` if you add a `pyproject.toml`.

### `scripts/`

- **`deploy.sh`** — creates a versioned staging S3 bucket (idempotent),
  zips `lambda-mcp/mcp_app.py`, uploads it, deploys the CFN stack, forces
  `lambda update-function-code` so the handler picks up new code even when
  CFN sees no config change, then writes stack outputs into `.env`. Region
  comes from `AWS_REGION` env var or `aws configure get region`.
- **`teardown.sh`** — deletes the CFN stack and waits for completion. When
  `DELETE_BUCKET=1` it also purges the staging bucket's object versions and
  delete markers (required for versioned buckets) and deletes it. The
  CloudWatch log group for the Lambda persists by default.

---

## Quickstart

```bash
# From sigv4-mcp-bridge/

# 1. Deploy the stack
./scripts/deploy.sh

# 2. Load stack outputs
source .env

# 3. Install deps into a local venv
python3 -m venv .venv && source .venv/bin/activate
pip install -r sigv4_mcp_bridge/requirements.txt \
            -r agent/requirements.txt

# 4. Smoke test (no Bedrock needed)
PYTHONPATH=. python agent/smoke_test.py
# → tools/list: ['echo', 'whoami', 'list_caller_identity']
# → whoami: {'status': 'success', ..., 'content': [{'text': 'arn:aws:sts::…:assumed-role/…'}], …}

# 5. Full agent (requires Bedrock access to Claude Haiku 4.5 in your region)
PYTHONPATH=. python agent/agent.py "What's my caller identity? Use the whoami tool."
# → Your caller identity is: arn:aws:sts::123456789012:assumed-role/Admin/example-user
```

---

## Using the bridge in your own agent

Once the bridge is installed, the integration is three lines:

```python
from sigv4_mcp_bridge import strands_mcp_client
from strands import Agent

mcp = strands_mcp_client(
    url="https://<fn-url>.lambda-url.<region>.on.aws/mcp",
    service="lambda",
    region="us-west-2",
)

with mcp:
    agent = Agent(
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
        tools=mcp.list_tools_sync(),
    )
    print(agent("Call the whoami tool."))
```

The `with mcp:` context manager opens the signed HTTP session for the
duration of the agent's run. Credentials are resolved on each request, so
if you're using STS assume-role with short-lived credentials the bridge
picks up rotations without restart.

Other AWS-hosted MCP targets work the same way with a different `service=`
value:

| Target | `service` | Notes |
|---|---|---|
| Lambda Function URL | `lambda` | This project |
| API Gateway (REST or HTTP) with IAM auth | `execute-api` | URL must include stage + resource path |
| AgentCore Runtime | `bedrock-agentcore` | See `12_agentic/mcp/` for the server side |
| AppRunner with IAM auth | `apprunner` | |
| Custom service behind ALB with IAM | depends | Usually fronted by API Gateway in practice |

### Cross-account calls

To have an agent in account A call an MCP server in account B, two things
need to happen:

1. **In account B (MCP side)**: give account A's role permission to invoke
   the Function URL. Either widen the `McpFunctionUrlPermission` principal
   to `arn:aws:iam::<A>:root`, or add the caller's role ARN explicitly.
2. **In account A (agent side)**: assume a role (or use any principal) that
   has `lambda:InvokeFunctionUrl` on the account-B ARN. Pass the resulting
   `botocore.credentials.Credentials` to the factory:

   ```python
   import boto3
   creds = boto3.Session().get_credentials()   # or an assumed-role session
   mcp = strands_mcp_client(url=..., credentials=creds, region=...)
   ```

---

## Verifying by hand

Useful when something's off or you want to see the raw wire format.

**Confirm the Function URL rejects unsigned requests**
```bash
curl -si -X POST "$MCP_FUNCTION_URL/mcp" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}'
# → HTTP/1.1 403 Forbidden
# → x-amzn-ErrorType: AccessDeniedException
# → {"Message":"Forbidden"}
```

**Sign a request manually with `awscurl`**
```bash
pip install awscurl
awscurl --service lambda --region us-west-2 \
  -X POST "$MCP_FUNCTION_URL/mcp" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# → {"jsonrpc":"2.0","id":1,"result":{"tools":[…]}}
```

**Inspect the signature the bridge generated**
Set `BOTO_LOG_LEVEL=DEBUG` or configure Python logging for `botocore.auth`:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger("botocore.auth").setLevel(logging.DEBUG)
```

You'll see the canonical request and string-to-sign on every call, which
is invaluable for debugging `SignatureDoesNotMatch`.

**Tail Lambda logs**
```bash
aws logs tail /aws/lambda/sigv4-mcp-bridge-mcp --region us-west-2 --follow
```

---

## Security caveats

- **IAM policy specificity.** The stack's `CallerRole` grants
  `lambda:InvokeFunctionUrl` on this one function ARN, gated by
  `lambda:FunctionUrlAuthType: AWS_IAM`. If you reuse this role, keep the
  resource tight — `Resource: "*"` means any SigV4-authenticated caller can
  hit any Function URL in the account.
- **Clock skew.** SigV4 signatures are valid for ±5 minutes. Lambda, ECS,
  and EC2 have tight NTP; some developer laptops don't. `RequestTimeTooSkewed`
  means your system clock is drifting.
- **Credential caching.** The bridge uses the boto3 default credential
  chain, which caches STS credentials for the duration of their validity. If
  you assume roles with short sessions, the bridge refreshes automatically
  — but if your role chain has a custom `DurationSeconds`, make sure the
  agent's expected runtime fits within it.
- **Payload size.** SigV4 hashes the full body. For large MCP requests
  (e.g. uploading a big blob) this costs a SHA-256 pass. For normal
  JSON-RPC it's negligible.
- **No user-level identity.** SigV4 authenticates the *AWS principal* that
  signed the request. If the same role serves multiple end users, the MCP
  server sees them as identical. Add application-level user context
  (custom header, a JWT passed alongside, etc.) if you need per-user audit
  trails. CloudTrail will record the AWS principal per call; that's usually
  the right layer for audit.
- **VPC endpoint.** For purely-internal traffic, deploy the Function URL
  inside a VPC (via [Lambda VPC
  config](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html)
  and an appropriate VPC endpoint) so calls never traverse the public
  internet. That's one more CFN change, not included here.

---

## Troubleshooting

**`403 Forbidden` with `AccessDeniedException` and no body.**
IAM rejected the signature before the handler ran. Check in order: service
name is `lambda` (not `apigateway`), region matches the function's region,
the caller has `lambda:InvokeFunctionUrl` on the exact function ARN,
`Condition` on `lambda:FunctionUrlAuthType: AWS_IAM` matches, clock isn't
drifting.

**`TypeError: aws_iam_streamablehttp_client() got an unexpected keyword argument 'url'`.**
You're on a version of `mcp-proxy-for-aws` where the kwargs are named
`endpoint`, `aws_service`, `aws_region`, not `url`, `service`, `region`.
This project's factory already translates — if you wrote a custom factory,
use the real kwarg names. Inspect with:
```python
import inspect, mcp_proxy_for_aws.client as c
print(inspect.signature(c.aws_iam_streamablehttp_client))
```

**`ValidationException: Invocation of model ID … with on-demand throughput isn't supported.`**
Claude Haiku 4.5 and a few other models require an inference profile. Use
the region-prefixed ID (`us.anthropic.claude-haiku-4-5-20251001-v1:0`),
not the bare model ID. Strands surfaces a link to the docs when this hits.

**`AttributeError: 'MCPAgentTool' object has no attribute 'invoke'`.**
Strands's `MCPAgentTool` doesn't expose a direct `invoke`. Call tools
through the `MCPClient` instead: `mcp.call_tool_sync(tool_use_id=..., name=..., arguments={...})`.

**`SignatureDoesNotMatch`.**
Usually a mismatch between the canonical request the client built and what
AWS recomputed. Common causes: an HTTP proxy rewriting headers, gzip
applied after signing, `X-Amz-Security-Token` stripped by a load balancer.
Turn on `botocore.auth` debug logging and compare the two canonical
requests.

**`ExpiredToken`.**
The STS session the bridge cached expired. Re-auth your CLI (`aws sso
login` or new `aws-vault` session) — boto3 picks it up on the next request.

**`TooManyRequestsException`.**
Lambda concurrent-execution limit or Function URL throttling. Not a bridge
issue; raise the function's reserved concurrency or the account-level
concurrent-execution limit.

**Agent runs a tool but the model complains it can't pass arguments.**
Your tool's `inputSchema` probably has no `properties`. The model relies on
the schema to know what fields the tool accepts; `{"type": "object"}` with
no properties means "no arguments." Add real schemas with `description`
fields; MCP's `tools/list` includes these and the LLM reads them.

**Bucket delete fails with `BucketNotEmpty` even after `s3 rm --recursive`.**
The staging bucket is versioned (by design, so updates are auditable).
`s3 rm` deletes current versions but leaves delete markers and old versions
behind. `teardown.sh` with `DELETE_BUCKET=1` now handles this correctly
using `list_object_versions` + `delete_objects` in bulk.

---

## Test notes from a real run

Verifying end-to-end against live AWS in `us-west-2` (account
`123456789012`, caller `arn:aws:sts::123456789012:assumed-role/Admin/example-user`):

1. `./scripts/deploy.sh` → stack up, `.env` written, `mcp_app.zip`
   uploaded to the staging bucket.
2. Unsigned `curl POST /mcp` → `403 Forbidden / AccessDeniedException`.
   IAM auth is enforced before the handler.
3. `PYTHONPATH=. python agent/smoke_test.py` → `tools/list` returns all
   three tools; `whoami` returns the caller's assumed-role ARN. SigV4
   signing works with the bridge's factory.
4. `PYTHONPATH=. python agent/agent.py "What's my caller identity? Use the
   whoami tool."` → Claude Haiku 4.5 calls `whoami`, returns the ARN.
5. `PYTHONPATH=. python agent/agent.py "Echo 'hello from sigv4' back to me
   using the echo tool."` → Claude Haiku 4.5 calls `echo` with
   `{"text": "hello from sigv4"}`, gets the string back. Confirms
   arguments flow through the LLM → MCP → Lambda → LLM round-trip.
6. `DELETE_BUCKET=1 ./scripts/teardown.sh` → stack deleted, bucket purged.

Total wall-clock time: ~5 minutes including a first-run deploy. Steady-state
`agent.py` round-trip is ~3 seconds (Bedrock Haiku inference dominates;
Lambda cold start is ~150 ms).

---

## Teardown

```bash
./scripts/teardown.sh                     # delete the stack
DELETE_BUCKET=1 ./scripts/teardown.sh     # also purge + delete staging bucket
rm -f .env
rm -rf .venv
```

Removes the Lambda, Function URL, both IAM roles, and (with
`DELETE_BUCKET=1`) the staging S3 bucket. CloudWatch log group persists by
default — delete it manually if you want a fully clean slate:

```bash
aws logs delete-log-group --log-group-name /aws/lambda/sigv4-mcp-bridge-mcp --region us-west-2
```
