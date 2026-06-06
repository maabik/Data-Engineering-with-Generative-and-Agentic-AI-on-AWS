"""Tests for breaker/gateway.py using moto DynamoDB."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from unittest.mock import MagicMock
import boto3
import pytest

try:
    from moto import mock_aws
    _MOTO = True
except ImportError:
    _MOTO = False

from breaker.budget import BudgetKey, BudgetPeriod
from breaker.enforcer import BudgetLimit, BudgetState
from breaker.gateway import BudgetGateway, _estimate_cost

pytestmark = pytest.mark.skipif(not _MOTO, reason="moto not installed")

_TABLE = "test-budgets"
_NOW = datetime(2024, 3, 15, 10, 0, 0, tzinfo=timezone.utc)


def _create_table(ddb):
    ddb.create_table(
        TableName=_TABLE,
        KeySchema=[
            {"AttributeName": "pk", "KeyType": "HASH"},
            {"AttributeName": "sk", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "pk", "AttributeType": "S"},
            {"AttributeName": "sk", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


def _bedrock_response(text="ok", input_t=100, output_t=50):
    return {
        "output": {"message": {"role": "assistant",
                               "content": [{"text": text}]}},
        "usage": {"inputTokens": input_t, "outputTokens": output_t,
                  "totalTokens": input_t + output_t},
        "stopReason": "end_turn",
    }


@pytest.fixture
def ddb():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        _create_table(resource)
        yield resource


@pytest.fixture
def gateway(ddb):
    bedrock = MagicMock()
    bedrock.converse.return_value = _bedrock_response()
    gw = BudgetGateway(
        bedrock_client=bedrock,
        table_name=_TABLE,
        dynamodb_resource=ddb,
    )
    return gw, bedrock, ddb


def _write_limit(gw, key, limit):
    gw._enforcer._counters._table.put_item(Item={
        "pk": key.pk,
        "sk": "LIMIT",
        "token_limit": limit.token_limit,
        "cost_limit_usd_micro": int(limit.cost_limit_usd * 1_000_000),
        "throttle_pct": str(limit.throttle_pct),
        "throttle_model_id": limit.throttle_model_id,
        "period": limit.period.value,
        "admin_contact": limit.admin_contact,
        "notification_arn": limit.notification_arn,
    })


# ── Pass-through (OK state / no limit) ────────────────────────────────────────

class TestPassThrough:
    def test_no_limit_calls_bedrock(self, gateway):
        gw, bedrock, _ = gateway
        key = BudgetKey("acme")
        result = gw.converse(budget_key=key, now=_NOW,
                             modelId="amazon.nova-micro-v1:0",
                             messages=[])
        bedrock.converse.assert_called_once()
        assert result.response["stopReason"] == "end_turn"

    def test_ok_state_calls_bedrock(self, gateway):
        gw, bedrock, _ = gateway
        key = BudgetKey("acme")
        _write_limit(gw, key, BudgetLimit(token_limit=100_000))
        result = gw.converse(budget_key=key, now=_NOW,
                             modelId="amazon.nova-micro-v1:0",
                             messages=[])
        bedrock.converse.assert_called_once()
        assert not result.was_blocked
        assert not result.was_throttled

    def test_tokens_recorded(self, gateway):
        gw, bedrock, _ = gateway
        bedrock.converse.return_value = _bedrock_response(input_t=200, output_t=80)
        key = BudgetKey("acme")
        result = gw.converse(budget_key=key, now=_NOW,
                             modelId="amazon.nova-micro-v1:0",
                             messages=[])
        assert result.input_tokens == 200
        assert result.output_tokens == 80

    def test_cost_estimated(self, gateway):
        gw, bedrock, _ = gateway
        bedrock.converse.return_value = _bedrock_response(input_t=1000, output_t=1000)
        key = BudgetKey("acme")
        result = gw.converse(budget_key=key, now=_NOW,
                             modelId="amazon.nova-micro-v1:0",
                             messages=[])
        assert result.estimated_cost_usd > 0

    def test_model_id_requested_recorded(self, gateway):
        gw, bedrock, _ = gateway
        key = BudgetKey("acme")
        result = gw.converse(budget_key=key, now=_NOW,
                             modelId="us.anthropic.claude-sonnet-4-6",
                             messages=[])
        assert result.model_id_requested == "us.anthropic.claude-sonnet-4-6"


# ── THROTTLE behaviour ────────────────────────────────────────────────────────

class TestThrottle:
    def test_throttled_switches_model(self, gateway):
        gw, bedrock, _ = gateway
        key = BudgetKey("acme")
        _write_limit(gw, key, BudgetLimit(
            token_limit=100_000,
            throttle_pct=0.80,
            throttle_model_id="amazon.nova-micro-v1:0",
        ))
        gw._enforcer._counters.increment(
            key, __import__("breaker.budget", fromlist=["UsageRecord"]).UsageRecord(85_000, 0, 0.0),
            [BudgetPeriod.MONTHLY], _NOW
        )
        result = gw.converse(budget_key=key, now=_NOW,
                             modelId="us.anthropic.claude-sonnet-4-6",
                             messages=[])
        assert result.was_throttled
        assert result.model_id_used == "amazon.nova-micro-v1:0"
        assert result.model_id_requested == "us.anthropic.claude-sonnet-4-6"

    def test_throttled_still_calls_bedrock(self, gateway):
        gw, bedrock, _ = gateway
        key = BudgetKey("acme")
        _write_limit(gw, key, BudgetLimit(
            token_limit=100_000,
            throttle_pct=0.80,
            throttle_model_id="amazon.nova-micro-v1:0",
        ))
        from breaker.budget import UsageRecord
        gw._enforcer._counters.increment(
            key, UsageRecord(85_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        gw.converse(budget_key=key, now=_NOW,
                    modelId="us.anthropic.claude-sonnet-4-6",
                    messages=[])
        bedrock.converse.assert_called_once()
        # Verify the override model was passed to Bedrock
        call_kwargs = bedrock.converse.call_args[1]
        assert call_kwargs["modelId"] == "amazon.nova-micro-v1:0"

    def test_throttle_without_override_model_uses_original(self, gateway):
        gw, bedrock, _ = gateway
        key = BudgetKey("acme")
        _write_limit(gw, key, BudgetLimit(
            token_limit=100_000,
            throttle_pct=0.80,
            throttle_model_id="",  # no override
        ))
        from breaker.budget import UsageRecord
        gw._enforcer._counters.increment(
            key, UsageRecord(85_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        result = gw.converse(budget_key=key, now=_NOW,
                             modelId="us.anthropic.claude-sonnet-4-6",
                             messages=[])
        assert result.model_id_used == "us.anthropic.claude-sonnet-4-6"


# ── BLOCK behaviour ───────────────────────────────────────────────────────────

class TestBlock:
    def test_blocked_does_not_call_bedrock(self, gateway):
        gw, bedrock, _ = gateway
        key = BudgetKey("acme")
        _write_limit(gw, key, BudgetLimit(token_limit=1_000))
        from breaker.budget import UsageRecord
        gw._enforcer._counters.increment(
            key, UsageRecord(1_001, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        result = gw.converse(budget_key=key, now=_NOW,
                             modelId="amazon.nova-micro-v1:0",
                             messages=[])
        bedrock.converse.assert_not_called()
        assert result.was_blocked

    def test_blocked_response_has_budget_stop_reason(self, gateway):
        gw, bedrock, _ = gateway
        key = BudgetKey("acme")
        _write_limit(gw, key, BudgetLimit(token_limit=100))
        from breaker.budget import UsageRecord
        gw._enforcer._counters.increment(
            key, UsageRecord(101, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        result = gw.converse(budget_key=key, now=_NOW,
                             modelId="amazon.nova-micro-v1:0",
                             messages=[])
        assert result.response["stopReason"] == "budget_limit_reached"

    def test_blocked_response_text_mentions_limit(self, gateway):
        gw, bedrock, _ = gateway
        key = BudgetKey("acme")
        _write_limit(gw, key, BudgetLimit(
            token_limit=100, admin_contact="ops@example.com"
        ))
        from breaker.budget import UsageRecord
        gw._enforcer._counters.increment(
            key, UsageRecord(101, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        result = gw.converse(budget_key=key, now=_NOW,
                             modelId="amazon.nova-micro-v1:0",
                             messages=[])
        text = result.response["output"]["message"]["content"][0]["text"]
        assert "limit" in text.lower()
        assert "ops@example.com" in text

    def test_blocked_zero_cost(self, gateway):
        gw, bedrock, _ = gateway
        key = BudgetKey("acme")
        _write_limit(gw, key, BudgetLimit(token_limit=100))
        from breaker.budget import UsageRecord
        gw._enforcer._counters.increment(
            key, UsageRecord(101, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        result = gw.converse(budget_key=key, now=_NOW,
                             modelId="amazon.nova-micro-v1:0",
                             messages=[])
        assert result.estimated_cost_usd == 0.0


# ── Cost estimation ───────────────────────────────────────────────────────────

class TestCostEstimation:
    def test_nova_micro_pricing(self):
        cost = _estimate_cost("amazon.nova-micro-v1:0", 1000, 1000)
        expected = (1.0 * 0.000035) + (1.0 * 0.00014)
        assert abs(cost - expected) < 1e-9

    def test_sonnet_pricing(self):
        cost = _estimate_cost("us.anthropic.claude-sonnet-4-6", 1000, 500)
        expected = (1.0 * 0.003) + (0.5 * 0.015)
        assert abs(cost - expected) < 1e-9

    def test_unknown_model_zero(self):
        assert _estimate_cost("unknown.model-xyz", 1000, 1000) == 0.0
