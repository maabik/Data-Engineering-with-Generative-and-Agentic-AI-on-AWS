"""Tests for breaker/enforcer.py using moto DynamoDB + SNS."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import boto3
import pytest

try:
    from moto import mock_aws
    _MOTO = True
except ImportError:
    _MOTO = False

from breaker.budget import BudgetKey, BudgetPeriod, UsageRecord
from breaker.enforcer import (
    BudgetEnforcer, BudgetLimit, BudgetState, EnforcerDecision
)

pytestmark = pytest.mark.skipif(not _MOTO, reason="moto not installed")

_TABLE = "test-budgets"
_NOW = datetime(2024, 3, 15, 10, 0, 0, tzinfo=timezone.utc)


def _create_table(ddb_resource):
    ddb_resource.create_table(
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


@pytest.fixture
def ddb():
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        _create_table(resource)
        yield resource


@pytest.fixture
def sns(ddb):
    with mock_aws():
        client = boto3.client("sns", region_name="us-east-1")
        yield client


@pytest.fixture
def enforcer(ddb):
    return BudgetEnforcer(table_name=_TABLE, dynamodb_resource=ddb)


def _write_limit(enforcer, key, limit):
    """Write a limit directly via the internal table."""
    enforcer._counters._table.put_item(Item={
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


# ── No limit configured ───────────────────────────────────────────────────────

class TestNoLimit:
    def test_unknown_when_no_limit(self, enforcer):
        key = BudgetKey("acme")
        d = enforcer.check(key, _NOW)
        assert d.state == BudgetState.UNKNOWN

    def test_record_without_limit_returns_unknown(self, enforcer):
        key = BudgetKey("acme")
        d = enforcer.record(key, UsageRecord(100, 50, 0.005), _NOW)
        assert d.state == BudgetState.UNKNOWN


# ── OK state ──────────────────────────────────────────────────────────────────

class TestOKState:
    def test_below_threshold_is_ok(self, enforcer):
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(
            token_limit=100_000, throttle_pct=0.80
        ))
        # Use 50% of limit
        enforcer._counters.increment(
            key, UsageRecord(50_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert d.state == BudgetState.OK
        assert d.token_pct == pytest.approx(0.5, rel=0.01)

    def test_ok_has_snapshot(self, enforcer):
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(token_limit=100_000))
        d = enforcer.check(key, _NOW)
        assert d.snapshot is not None


# ── THROTTLED state ───────────────────────────────────────────────────────────

class TestThrottledState:
    def test_at_80pct_is_throttled(self, enforcer):
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(
            token_limit=100_000,
            throttle_pct=0.80,
            throttle_model_id="amazon.nova-micro-v1:0",
        ))
        enforcer._counters.increment(
            key, UsageRecord(80_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert d.state == BudgetState.THROTTLED
        assert d.override_model_id == "amazon.nova-micro-v1:0"

    def test_just_below_throttle_is_ok(self, enforcer):
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(
            token_limit=100_000, throttle_pct=0.80
        ))
        enforcer._counters.increment(
            key, UsageRecord(79_999, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert d.state == BudgetState.OK

    def test_throttle_triggered_by_cost(self, enforcer):
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(
            cost_limit_usd=10.0,
            throttle_pct=0.80,
            throttle_model_id="amazon.nova-micro-v1:0",
        ))
        enforcer._counters.increment(
            key, UsageRecord(0, 0, 8.5), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert d.state == BudgetState.THROTTLED
        assert d.cost_pct == pytest.approx(0.85, rel=0.01)

    def test_should_throttle_property(self, enforcer):
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(
            token_limit=100_000, throttle_pct=0.80
        ))
        enforcer._counters.increment(
            key, UsageRecord(85_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert d.should_throttle


# ── BLOCKED state ─────────────────────────────────────────────────────────────

class TestBlockedState:
    def test_at_100pct_is_blocked(self, enforcer):
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(
            token_limit=100_000, admin_contact="admin@acme.com"
        ))
        enforcer._counters.increment(
            key, UsageRecord(100_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert d.state == BudgetState.BLOCKED
        assert d.should_block

    def test_block_message_contains_contact(self, enforcer):
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(
            token_limit=100_000, admin_contact="admin@acme.com"
        ))
        enforcer._counters.increment(
            key, UsageRecord(100_001, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert "admin@acme.com" in d.block_message

    def test_block_message_contains_tenant(self, enforcer):
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(token_limit=1000))
        enforcer._counters.increment(
            key, UsageRecord(1001, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert "acme" in d.block_message

    def test_blocked_by_cost(self, enforcer):
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(cost_limit_usd=5.0))
        enforcer._counters.increment(
            key, UsageRecord(0, 0, 5.01), [BudgetPeriod.MONTHLY], _NOW
        )
        d = enforcer.check(key, _NOW)
        assert d.state == BudgetState.BLOCKED


# ── record() + SNS notification ───────────────────────────────────────────────

class TestRecordAndNotify:
    def test_record_increments_usage(self, enforcer):
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(token_limit=100_000))
        enforcer.record(key, UsageRecord(500, 200, 0.005), _NOW)
        snap = enforcer._counters.get(key, BudgetPeriod.MONTHLY, _NOW)
        assert snap.tokens_in == 500
        assert snap.tokens_out == 200

    def test_sns_notified_on_throttle(self, ddb):
        sns_client = MagicMock()
        enforcer = BudgetEnforcer(
            table_name=_TABLE, dynamodb_resource=ddb, sns_client=sns_client
        )
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(
            token_limit=100_000,
            throttle_pct=0.80,
            notification_arn="arn:aws:sns:us-east-1:123:budget-alerts",
        ))
        # Start just below threshold, then push over
        enforcer._counters.increment(
            key, UsageRecord(79_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        enforcer.record(key, UsageRecord(5_000, 0, 0.0), _NOW)
        sns_client.publish.assert_called_once()

    def test_sns_not_called_when_no_arn(self, ddb):
        sns_client = MagicMock()
        enforcer = BudgetEnforcer(
            table_name=_TABLE, dynamodb_resource=ddb, sns_client=sns_client
        )
        key = BudgetKey("acme")
        _write_limit(enforcer, key, BudgetLimit(
            token_limit=100_000,
            throttle_pct=0.80,
            notification_arn="",  # no ARN
        ))
        enforcer._counters.increment(
            key, UsageRecord(85_000, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        enforcer.record(key, UsageRecord(1_000, 0, 0.0), _NOW)
        sns_client.publish.assert_not_called()


# ── User-level isolation ──────────────────────────────────────────────────────

class TestUserIsolation:
    def test_user_budget_isolated_from_tenant(self, enforcer):
        tenant_key = BudgetKey("acme")
        user_key = BudgetKey("acme", user_id="alice")
        _write_limit(enforcer, user_key, BudgetLimit(token_limit=1_000))
        enforcer._counters.increment(
            user_key, UsageRecord(1_001, 0, 0.0), [BudgetPeriod.MONTHLY], _NOW
        )
        user_d = enforcer.check(user_key, _NOW)
        tenant_d = enforcer.check(tenant_key, _NOW)
        assert user_d.state == BudgetState.BLOCKED
        assert tenant_d.state == BudgetState.UNKNOWN  # no limit set for tenant
