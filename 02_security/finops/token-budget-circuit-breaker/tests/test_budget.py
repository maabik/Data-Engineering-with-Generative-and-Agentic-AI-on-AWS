"""Tests for breaker/budget.py using moto DynamoDB."""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
import boto3
import pytest

try:
    from moto import mock_aws
    _MOTO = True
except ImportError:
    _MOTO = False

from breaker.budget import (
    BudgetCounters, BudgetKey, BudgetPeriod, UsageRecord, BudgetSnapshot
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
def counters(ddb):
    return BudgetCounters(table_name=_TABLE, dynamodb_resource=ddb)


# ── BudgetKey ─────────────────────────────────────────────────────────────────

class TestBudgetKey:
    def test_tenant_only_pk(self):
        k = BudgetKey(tenant_id="acme")
        assert k.pk == "BUDGET#acme"

    def test_user_pk(self):
        k = BudgetKey(tenant_id="acme", user_id="alice")
        assert k.pk == "BUDGET#acme#alice"

    def test_agent_pk(self):
        k = BudgetKey(tenant_id="acme", user_id="alice", agent_id="agent-1")
        assert k.pk == "BUDGET#acme#alice#agent-1"

    def test_monthly_sk(self):
        k = BudgetKey(tenant_id="acme")
        sk = k.sk(BudgetPeriod.MONTHLY, _NOW)
        assert sk == "PERIOD#2024-03"

    def test_daily_sk(self):
        k = BudgetKey(tenant_id="acme")
        sk = k.sk(BudgetPeriod.DAILY, _NOW)
        assert sk == "PERIOD#2024-03-15"

    def test_weekly_sk(self):
        k = BudgetKey(tenant_id="acme")
        sk = k.sk(BudgetPeriod.WEEKLY, _NOW)
        # 2024-03-15 is week 11
        assert "W11" in sk


# ── BudgetCounters ─────────────────────────────────────────────────────────────

class TestBudgetCounters:
    def test_get_empty_returns_zeros(self, counters):
        snap = counters.get(BudgetKey("acme"), BudgetPeriod.MONTHLY, _NOW)
        assert snap.tokens_in == 0
        assert snap.tokens_out == 0
        assert snap.cost_usd == 0.0

    def test_increment_then_get(self, counters):
        key = BudgetKey("acme")
        counters.increment(key, UsageRecord(100, 50, 0.005), [BudgetPeriod.MONTHLY], _NOW)
        snap = counters.get(key, BudgetPeriod.MONTHLY, _NOW)
        assert snap.tokens_in == 100
        assert snap.tokens_out == 50
        assert abs(snap.cost_usd - 0.005) < 1e-6

    def test_increment_is_additive(self, counters):
        key = BudgetKey("acme")
        counters.increment(key, UsageRecord(100, 50, 0.005), [BudgetPeriod.MONTHLY], _NOW)
        counters.increment(key, UsageRecord(200, 100, 0.010), [BudgetPeriod.MONTHLY], _NOW)
        snap = counters.get(key, BudgetPeriod.MONTHLY, _NOW)
        assert snap.tokens_in == 300
        assert snap.tokens_out == 150

    def test_cost_stored_as_integer_micros(self, counters):
        key = BudgetKey("acme")
        counters.increment(key, UsageRecord(0, 0, 0.123456), [BudgetPeriod.MONTHLY], _NOW)
        snap = counters.get(key, BudgetPeriod.MONTHLY, _NOW)
        assert abs(snap.cost_usd - 0.123456) < 1e-6

    def test_different_periods_independent(self, counters):
        key = BudgetKey("acme")
        counters.increment(key, UsageRecord(100, 0, 0.001),
                           [BudgetPeriod.MONTHLY, BudgetPeriod.DAILY], _NOW)
        monthly = counters.get(key, BudgetPeriod.MONTHLY, _NOW)
        daily = counters.get(key, BudgetPeriod.DAILY, _NOW)
        assert monthly.tokens_in == 100
        assert daily.tokens_in == 100

    def test_different_tenants_isolated(self, counters):
        counters.increment(BudgetKey("acme"), UsageRecord(100, 0, 0.001),
                           [BudgetPeriod.MONTHLY], _NOW)
        snap_other = counters.get(BudgetKey("globex"), BudgetPeriod.MONTHLY, _NOW)
        assert snap_other.tokens_in == 0

    def test_total_tokens_property(self, counters):
        key = BudgetKey("acme")
        counters.increment(key, UsageRecord(300, 150, 0.005), [BudgetPeriod.MONTHLY], _NOW)
        snap = counters.get(key, BudgetPeriod.MONTHLY, _NOW)
        assert snap.total_tokens == 450

    def test_reset_clears_counter(self, counters):
        key = BudgetKey("acme")
        counters.increment(key, UsageRecord(100, 50, 0.005), [BudgetPeriod.MONTHLY], _NOW)
        counters.reset(key, BudgetPeriod.MONTHLY, _NOW)
        snap = counters.get(key, BudgetPeriod.MONTHLY, _NOW)
        assert snap.tokens_in == 0
