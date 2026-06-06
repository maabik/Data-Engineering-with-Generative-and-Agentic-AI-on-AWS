# token-budget-circuit-breaker

## 1 — Overview

Real-time per-tenant, per-user, per-agent token and dollar budget enforcement at the gateway layer. 80% of monthly budget → throttle to cheaper model. 100% → graceful block response (no Bedrock call). DynamoDB atomic counters + EventBridge auto-recording + SNS threshold alerts.

## 2 — Architecture (ASCII)

```
Request
  │
  ▼
BudgetGateway.converse(budget_key, **kwargs)
  │
  ├── 1. BudgetEnforcer.check()
  │       DynamoDB consistent read → BudgetSnapshot
  │       compare against stored BudgetLimit
  │       │
  │       ├── OK         → proceed with original model
  │       ├── THROTTLED  → swap modelId to throttle_model_id
  │       └── BLOCKED    → return synthetic limit-reached response (no Bedrock call)
  │
  ├── 2. bedrock-runtime.converse(**kwargs)  [skipped if BLOCKED]
  │
  └── 3. BudgetEnforcer.record()
          ADD tokens + cost to DynamoDB counter (atomic, no OCC needed)
          Re-evaluate state → publish SNS if threshold just crossed
```

## 3 — Prerequisites

- Python >= 3.11
- `boto3`, `moto[dynamodb,sns]` for tests
- AWS account, region `us-west-2`
- CDK CLI + bootstrap

## 4 — Installation

```bash
pip install -e .
pip install -e ".[dev]"    # adds pytest, moto
```

## 5 — Quick start

### Set a budget limit

```python
import boto3
from breaker import BudgetGateway, BudgetKey
from breaker.enforcer import BudgetEnforcer, BudgetLimit
from breaker.budget import BudgetPeriod

enforcer = BudgetEnforcer(table_name="ai-token-budgets")
enforcer.set_limit(
    BudgetKey(tenant_id="acme"),
    BudgetLimit(
        token_limit=1_000_000,          # 1M tokens/month
        cost_limit_usd=50.0,            # $50/month (whichever hits first)
        throttle_pct=0.80,              # throttle at 80%
        throttle_model_id="amazon.nova-micro-v1:0",
        period=BudgetPeriod.MONTHLY,
        admin_contact="ops@acme.com",
        notification_arn="arn:aws:sns:us-east-1:123:budget-alerts",
    ),
)
```

### Use as a drop-in wrapper

```python
import boto3
from breaker import BudgetGateway, BudgetKey

bedrock = boto3.client("bedrock-runtime")
gateway = BudgetGateway(bedrock_client=bedrock, table_name="ai-token-budgets")

result = gateway.converse(
    budget_key=BudgetKey(tenant_id="acme", user_id="alice"),
    modelId="us.anthropic.claude-sonnet-4-6",
    messages=[{"role": "user", "content": [{"text": "What is the capital of France?"}]}],
)

# Standard Bedrock response — same shape as bedrock-runtime.converse()
print(result.response["output"]["message"]["content"][0]["text"])

# Routing metadata
print(f"Model used:    {result.model_id_used}")
print(f"Model wanted:  {result.model_id_requested}")
print(f"Was throttled: {result.was_throttled}")
print(f"Was blocked:   {result.was_blocked}")
print(f"Cost:          ${result.estimated_cost_usd:.6f}")
print(f"Budget state:  {result.decision.state.value}")
print(f"Token usage:   {result.decision.token_pct:.0%} of limit")
```

### Check budget without invoking

```python
from breaker.enforcer import BudgetEnforcer
from breaker.budget import BudgetKey

enforcer = BudgetEnforcer(table_name="ai-token-budgets")
decision = enforcer.check(BudgetKey(tenant_id="acme", user_id="alice"))

if decision.should_block:
    return {"error": decision.block_message}
if decision.should_throttle:
    model_id = decision.override_model_id
```

## 6 — Budget states

| State | Condition | Action |
|---|---|---|
| `ok` | usage < throttle_pct | Call original model |
| `throttled` | throttle_pct <= usage < 100% | Swap to `throttle_model_id` |
| `blocked` | usage >= 100% | Return graceful message, skip Bedrock |
| `unknown` | No limit configured | Pass through (fail open) |

Note: state `unknown` means no limit is configured — the request passes through (fail-open). Set limits explicitly to enforce hard caps.

## 7 — Budget periods

`MONTHLY` (default), `DAILY`, `WEEKLY`. Limits and counters are independent per period. Set both a monthly cap and a daily spike limit:

```python
enforcer.set_limit(
    BudgetKey(tenant_id="acme"),
    BudgetLimit(token_limit=50_000, period=BudgetPeriod.DAILY, throttle_pct=0.90),
)
```

## 8 — SNS notifications

Set `notification_arn` on the `BudgetLimit`. The enforcer publishes to that topic whenever state crosses into `THROTTLED` or `BLOCKED`, with message attributes `state` and `tenant_id` for SNS filter policies:

```json
{
  "budget_key": "BUDGET#acme",
  "state": "throttled",
  "token_pct": 0.83,
  "cost_pct": 0.0,
  "period": "monthly"
}
```

## 9 — Inference profile IDs (important)

On-demand Bedrock throughput requires cross-region inference profile IDs. Use `us.amazon.nova-micro-v1:0`, not `amazon.nova-micro-v1:0`. The gateway's `_estimate_cost()` strips `us./eu./ap.` prefixes automatically, so pricing is always correct.

Example throttle config:

```python
BudgetLimit(
    token_limit=1_000_000,
    throttle_pct=0.80,
    throttle_model_id="us.amazon.nova-micro-v1:0",   # <- use inference profile
)
```

## 10 — Pairing with application inference profiles

Inference profiles let you tag Bedrock calls with cost-allocation tags. The `BudgetKey.pk` maps cleanly to a tag value:

```python
result = gateway.converse(
    budget_key=BudgetKey(tenant_id="acme", user_id="alice"),
    modelId="arn:aws:bedrock:us-east-1:123456789012:inference-profile/acme-sonnet",
    requestMetadata={
        "tenant_id": "acme",
        "user_id": "alice",
        "budget_key": "BUDGET#acme#alice",
    },
    messages=messages,
)
```

The `budget_key` in `requestMetadata` flows into Bedrock invocation logs, which the `RecorderFunction` Lambda reads back via EventBridge to keep counters in sync even for calls that bypass the gateway.

## 11 — DynamoDB table design

Single table `ai-token-budgets` with composite key `pk + sk`:

| pk | sk | Purpose |
|---|---|---|
| `BUDGET#acme` | `PERIOD#2024-03` | Monthly counters for tenant acme |
| `BUDGET#acme#alice` | `PERIOD#2024-03` | Monthly counters for user alice |
| `BUDGET#acme#alice#agent-1` | `PERIOD#2024-03` | Monthly counters for a specific agent |
| `BUDGET#acme` | `LIMIT` | Budget limit config for tenant acme |
| `BUDGET#acme#alice` | `LIMIT` | Per-user limit override |

Counter attributes: `tokens_in`, `tokens_out`, `cost_usd_micro` (integer microdollars to avoid float drift), `ttl` (auto-deleted after 90 days).

All counter writes use DynamoDB `ADD` — concurrent Lambda invocations are race-free with no optimistic concurrency overhead.

## 12 — EventBridge auto-recording

The CDK stack deploys a `RecorderFunction` Lambda that listens for Bedrock CloudTrail events on EventBridge. For calls that go through the gateway, usage is recorded synchronously. For calls that bypass the gateway (direct Bedrock calls by other Lambda functions), `requestMetadata.tenant_id` is extracted from the CloudTrail event and usage is back-filled automatically.

## 13 — CDK deployment

**Stack name: `BudgetCircuitBreaker`** (not `BudgetCircuitBreakerStack`).

```bash
cd cdk
pip install aws-cdk-lib constructs
cdk bootstrap aws://ACCOUNT_ID/us-west-2   # first time only
cdk deploy BudgetCircuitBreaker
```

Resources provisioned:

| Resource | Details |
|---|---|
| DynamoDB table | `ai-token-budgets`, on-demand billing, TTL on `ttl` attribute, PITR enabled, RETAIN on delete |
| SNS topic | `ai-budget-alerts` |
| Lambda | `ai-budget-recorder` (Python 3.12, 30s timeout, 256 MB) |
| EventBridge rule | Routes Bedrock `InvokeModel`/`Converse` CloudTrail events → Lambda |
| CW alarm | DynamoDB write throttles → SNS |

Lambda bundling note: the `breaker/` package is copied into `lambda/recorder/breaker/` so it is included in the Lambda zip asset. No Lambda layer is required.

CloudFormation outputs:

| Output | Value |
|---|---|
| `TableName` | `ai-token-budgets` |
| `AlertTopicArn` | SNS topic ARN |
| `RecorderFunctionArn` | Lambda function ARN |

## 14 — Unit tests

```bash
pytest tests/ -v    # 50 tests, uses moto mocks — no AWS credentials needed
```

## 15 — Live integration tests

Requires deployed `BudgetCircuitBreaker` stack and AWS credentials in `us-west-2`:

```bash
cd token-budget-circuit-breaker
pytest tests/test_live.py -v
```

12 tests:

- `test_table_active` — DynamoDB table status, billing mode, TTL enabled
- `test_counter_increment_and_read` — write + consistent read round-trip
- `test_counter_is_additive` — two increments → sum
- `test_enforcer_ok_state` — 50% usage → OK, correct `token_pct`
- `test_enforcer_throttle_state` — 85% usage → THROTTLED, `override_model_id` set
- `test_enforcer_block_state` — 101% usage → BLOCKED, `block_message` contains admin contact
- `test_gateway_ok_real_bedrock` — real Bedrock call (Nova Micro), response text present, cost > 0
- `test_gateway_throttle_real_bedrock` — requested Sonnet, routed to Nova Micro by budget
- `test_gateway_block_no_bedrock_call` — blocked budget, Bedrock mock never called
- `test_lambda_direct_invocation` — Lambda returns 200, `processed=True`, state in known set
- `test_lambda_no_tenant_skips` — empty `tenant_id` → `processed=False`
- `test_lambda_unknown_source_400` — unknown `source` → `statusCode=400`

All tests use `_NOW = datetime(2099, 1, 1)` as the period timestamp to avoid polluting real monthly counters, and clean up DynamoDB items in `finally` blocks.

## 16 — Project structure

```
token-budget-circuit-breaker/
├── breaker/
│   ├── __init__.py
│   ├── budget.py          # BudgetCounters, BudgetKey, BudgetPeriod, UsageRecord
│   ├── enforcer.py        # BudgetEnforcer, BudgetLimit, BudgetState, EnforcerDecision
│   └── gateway.py         # BudgetGateway, GatewayResult
├── lambda/
│   └── recorder/
│       ├── handler.py     # EventBridge + direct-invoke recorder
│       └── breaker/       # bundled copy of breaker/ for Lambda deployment
├── cdk/
│   ├── app.py
│   ├── cdk.json
│   └── stacks/
│       └── budget_stack.py    # BudgetCircuitBreakerStack
├── tests/
│   ├── test_budget.py         # 14 unit tests
│   ├── test_enforcer.py       # 16 unit tests
│   ├── test_gateway.py        # 15 unit tests
│   ├── test_handler.py        # 5 unit tests
│   └── test_live.py           # 12 live tests
└── pyproject.toml
```
