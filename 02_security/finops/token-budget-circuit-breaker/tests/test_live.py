"""Live integration tests for the deployed BudgetCircuitBreaker stack.

Requires:
  AWS_REGION=us-west-2
  Deployed stack: BudgetCircuitBreaker

These tests create real DynamoDB items, verify state machine transitions,
invoke the deployed Lambda, and clean up after themselves.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
from datetime import datetime, timezone
import boto3
import pytest

REGION = "us-west-2"
TABLE  = "ai-token-budgets"
LAMBDA = "ai-budget-recorder"
_NOW   = datetime(2099, 1, 1, tzinfo=timezone.utc)  # far-future period → no pollution


@pytest.fixture(scope="module")
def ddb():
    return boto3.resource("dynamodb", region_name=REGION)

@pytest.fixture(scope="module")
def table(ddb):
    return ddb.Table(TABLE)

@pytest.fixture(scope="module")
def lambda_client():
    return boto3.client("lambda", region_name=REGION)


def _clean(table, pk):
    for sk in ["LIMIT", "PERIOD#2099-01", "PERIOD#2099-01-01"]:
        table.delete_item(Key={"pk": pk, "sk": sk})


# ── DynamoDB table exists and is ACTIVE ───────────────────────────────────────

def test_table_active():
    ddb_client = boto3.client("dynamodb", region_name=REGION)
    resp = ddb_client.describe_table(TableName=TABLE)
    assert resp["Table"]["TableStatus"] == "ACTIVE"
    assert resp["Table"]["BillingModeSummary"]["BillingMode"] == "PAY_PER_REQUEST"
    ttl = ddb_client.describe_time_to_live(TableName=TABLE)
    assert ttl["TimeToLiveDescription"]["TimeToLiveStatus"] == "ENABLED"


# ── BudgetCounters round-trip ─────────────────────────────────────────────────

def test_counter_increment_and_read(table):
    from breaker.budget import BudgetCounters, BudgetKey, BudgetPeriod, UsageRecord
    import boto3 as _b
    ddb = _b.resource("dynamodb", region_name=REGION)
    counters = BudgetCounters(TABLE, ddb)
    key = BudgetKey("live-test-tenant", user_id="live-test-user")
    _clean(table, key.pk)
    try:
        counters.increment(key, UsageRecord(100, 50, 0.005),
                           [BudgetPeriod.MONTHLY], _NOW)
        snap = counters.get(key, BudgetPeriod.MONTHLY, _NOW)
        assert snap.tokens_in == 100
        assert snap.tokens_out == 50
        assert abs(snap.cost_usd - 0.005) < 1e-5
    finally:
        _clean(table, key.pk)


def test_counter_is_additive(table):
    from breaker.budget import BudgetCounters, BudgetKey, BudgetPeriod, UsageRecord
    import boto3 as _b
    ddb = _b.resource("dynamodb", region_name=REGION)
    counters = BudgetCounters(TABLE, ddb)
    key = BudgetKey("live-additive-tenant")
    _clean(table, key.pk)
    try:
        counters.increment(key, UsageRecord(100, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW)
        counters.increment(key, UsageRecord(200, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW)
        snap = counters.get(key, BudgetPeriod.MONTHLY, _NOW)
        assert snap.tokens_in == 300
    finally:
        _clean(table, key.pk)


# ── Enforcer state machine against live DDB ───────────────────────────────────

def test_enforcer_ok_state(table):
    from breaker.budget import BudgetKey, BudgetPeriod, UsageRecord
    from breaker.enforcer import BudgetEnforcer, BudgetLimit, BudgetState
    import boto3 as _b
    ddb = _b.resource("dynamodb", region_name=REGION)
    enforcer = BudgetEnforcer(TABLE, ddb)
    key = BudgetKey("live-ok-tenant")
    _clean(table, key.pk)
    try:
        enforcer.set_limit(key, BudgetLimit(token_limit=100_000, throttle_pct=0.80))
        enforcer._counters.increment(
            key, UsageRecord(50_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert d.state == BudgetState.OK
        assert d.token_pct == pytest.approx(0.5, abs=0.01)
    finally:
        _clean(table, key.pk)


def test_enforcer_throttle_state(table):
    from breaker.budget import BudgetKey, BudgetPeriod, UsageRecord
    from breaker.enforcer import BudgetEnforcer, BudgetLimit, BudgetState
    import boto3 as _b
    ddb = _b.resource("dynamodb", region_name=REGION)
    enforcer = BudgetEnforcer(TABLE, ddb)
    key = BudgetKey("live-throttle-tenant")
    _clean(table, key.pk)
    try:
        enforcer.set_limit(key, BudgetLimit(
            token_limit=100_000,
            throttle_pct=0.80,
            throttle_model_id="us.amazon.nova-micro-v1:0",
        ))
        enforcer._counters.increment(
            key, UsageRecord(85_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert d.state == BudgetState.THROTTLED
        assert d.override_model_id == "us.amazon.nova-micro-v1:0"
    finally:
        _clean(table, key.pk)


def test_enforcer_block_state(table):
    from breaker.budget import BudgetKey, BudgetPeriod, UsageRecord
    from breaker.enforcer import BudgetEnforcer, BudgetLimit, BudgetState
    import boto3 as _b
    ddb = _b.resource("dynamodb", region_name=REGION)
    enforcer = BudgetEnforcer(TABLE, ddb)
    key = BudgetKey("live-block-tenant")
    _clean(table, key.pk)
    try:
        enforcer.set_limit(key, BudgetLimit(
            token_limit=100_000,
            admin_contact="ops@example.com",
        ))
        enforcer._counters.increment(
            key, UsageRecord(101_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert d.state == BudgetState.BLOCKED
        assert "ops@example.com" in d.block_message
    finally:
        _clean(table, key.pk)


# ── Gateway: real Bedrock call + budget enforced ──────────────────────────────

def test_gateway_ok_real_bedrock(table):
    from breaker.budget import BudgetKey, BudgetPeriod, UsageRecord
    from breaker.enforcer import BudgetEnforcer, BudgetLimit
    from breaker.gateway import BudgetGateway
    import boto3 as _b
    ddb  = _b.resource("dynamodb", region_name=REGION)
    brt  = _b.client("bedrock-runtime", region_name=REGION)
    gw   = BudgetGateway(brt, TABLE, ddb)
    enforcer = BudgetEnforcer(TABLE, ddb)
    key = BudgetKey("live-gw-ok-tenant")
    _clean(table, key.pk)
    try:
        enforcer.set_limit(key, BudgetLimit(token_limit=1_000_000))
        result = gw.converse(
            budget_key=key,
            now=_NOW,
            modelId="us.amazon.nova-micro-v1:0",
            messages=[{"role": "user", "content": [{"text": "Say OK"}]}],
            inferenceConfig={"maxTokens": 20},
        )
        assert not result.was_blocked
        assert not result.was_throttled
        assert result.input_tokens > 0
        assert result.output_tokens > 0
        assert result.estimated_cost_usd > 0
        text = result.response["output"]["message"]["content"][0]["text"]
        assert len(text) > 0
    finally:
        _clean(table, key.pk)


def test_gateway_throttle_real_bedrock(table):
    """At 85% usage, gateway should route to nova-micro even when sonnet was requested."""
    from breaker.budget import BudgetKey, BudgetPeriod, UsageRecord
    from breaker.enforcer import BudgetEnforcer, BudgetLimit
    from breaker.gateway import BudgetGateway
    import boto3 as _b
    ddb  = _b.resource("dynamodb", region_name=REGION)
    brt  = _b.client("bedrock-runtime", region_name=REGION)
    gw   = BudgetGateway(brt, TABLE, ddb)
    enforcer = BudgetEnforcer(TABLE, ddb)
    key = BudgetKey("live-gw-throttle-tenant")
    _clean(table, key.pk)
    try:
        enforcer.set_limit(key, BudgetLimit(
            token_limit=100_000,
            throttle_pct=0.80,
            throttle_model_id="us.amazon.nova-micro-v1:0",
        ))
        enforcer._counters.increment(
            key, UsageRecord(85_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        result = gw.converse(
            budget_key=key,
            now=_NOW,
            modelId="us.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": [{"text": "Say OK"}]}],
            inferenceConfig={"maxTokens": 20},
        )
        assert result.was_throttled
        assert result.model_id_requested == "us.anthropic.claude-sonnet-4-6"
        assert result.model_id_used == "us.amazon.nova-micro-v1:0"
        # Still got a real answer
        text = result.response["output"]["message"]["content"][0]["text"]
        assert len(text) > 0
    finally:
        _clean(table, key.pk)


def test_gateway_block_no_bedrock_call(table):
    """At 100%+ usage, no Bedrock call is made; block message is returned."""
    from breaker.budget import BudgetKey, BudgetPeriod, UsageRecord
    from breaker.enforcer import BudgetEnforcer, BudgetLimit
    from breaker.gateway import BudgetGateway
    from unittest.mock import MagicMock
    import boto3 as _b
    ddb  = _b.resource("dynamodb", region_name=REGION)
    bedrock_mock = MagicMock()  # must NOT be called
    gw   = BudgetGateway(bedrock_mock, TABLE, ddb)
    enforcer = BudgetEnforcer(TABLE, ddb)
    key = BudgetKey("live-gw-block-tenant")
    _clean(table, key.pk)
    try:
        enforcer.set_limit(key, BudgetLimit(
            token_limit=1_000,
            admin_contact="ops@test.com",
        ))
        enforcer._counters.increment(
            key, UsageRecord(1_001, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        result = gw.converse(
            budget_key=key,
            now=_NOW,
            modelId="us.amazon.nova-micro-v1:0",
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        )
        assert result.was_blocked
        bedrock_mock.converse.assert_not_called()
        assert result.response["stopReason"] == "budget_limit_reached"
        text = result.response["output"]["message"]["content"][0]["text"]
        assert "limit" in text.lower()
    finally:
        _clean(table, key.pk)


# ── Lambda recorder: direct invocation ───────────────────────────────────────

def test_lambda_direct_invocation(lambda_client, table):
    key_pk = "BUDGET#live-lambda-tenant"
    for sk in ["LIMIT", "PERIOD#2099-01"]:
        table.delete_item(Key={"pk": key_pk, "sk": sk})
    try:
        payload = {
            "source": "budget_gateway",
            "tenant_id": "live-lambda-tenant",
            "user_id": "test-user",
            "agent_id": "",
            "model_id": "us.amazon.nova-micro-v1:0",
            "input_tokens": 500,
            "output_tokens": 200,
            "cost_usd": 0.0001,
        }
        resp = lambda_client.invoke(
            FunctionName=LAMBDA,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode(),
        )
        body = json.loads(resp["Payload"].read())
        assert resp["StatusCode"] == 200
        assert "FunctionError" not in resp
        assert body["statusCode"] == 200
        assert body["processed"] is True
        assert body["state"] in ("ok", "throttled", "blocked", "unknown")
    finally:
        for sk in ["LIMIT", "PERIOD#2099-01"]:
            table.delete_item(Key={"pk": key_pk, "sk": sk})


def test_lambda_no_tenant_skips(lambda_client):
    payload = {
        "source": "budget_gateway",
        "tenant_id": "",
        "model_id": "us.amazon.nova-micro-v1:0",
        "input_tokens": 100,
        "output_tokens": 50,
    }
    resp = lambda_client.invoke(
        FunctionName=LAMBDA,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )
    body = json.loads(resp["Payload"].read())
    assert body["processed"] is False


def test_lambda_unknown_source_400(lambda_client):
    resp = lambda_client.invoke(
        FunctionName=LAMBDA,
        InvocationType="RequestResponse",
        Payload=json.dumps({"source": "bogus"}).encode(),
    )
    body = json.loads(resp["Payload"].read())
    assert body["statusCode"] == 400
