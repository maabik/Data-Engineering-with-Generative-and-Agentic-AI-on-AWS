"""DynamoDB-backed token and dollar budget counters.

Table schema (single table, PK + SK):
  PK  = "BUDGET#{tenant_id}"  (or "BUDGET#{tenant_id}#{user_id}")
  SK  = "PERIOD#{year}-{month}"      (monthly)
       | "PERIOD#{year}-{month}-{day}" (daily)
       | "PERIOD#{year}-W{week}"       (weekly)

Attributes:
  tokens_in        INT  — total input tokens consumed this period
  tokens_out       INT  — total output tokens consumed this period
  cost_usd_micro   INT  — cost * 1_000_000 (avoids float precision issues)
  updated_at       STR  — ISO UTC of last write
  ttl              INT  — epoch seconds; DynamoDB auto-deletes after 90 days

Atomic updates use ADD (not SET) so concurrent Lambda invocations never
lose counts — no OCC retries needed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class BudgetPeriod(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class BudgetKey:
    tenant_id: str
    user_id: Optional[str] = None
    agent_id: Optional[str] = None

    @property
    def pk(self) -> str:
        parts = ["BUDGET", self.tenant_id]
        if self.user_id:
            parts.append(self.user_id)
        if self.agent_id:
            parts.append(self.agent_id)
        return "#".join(parts)

    def sk(self, period: BudgetPeriod, now: Optional[datetime] = None) -> str:
        dt = now or datetime.now(timezone.utc)
        if period == BudgetPeriod.DAILY:
            return f"PERIOD#{dt.strftime('%Y-%m-%d')}"
        elif period == BudgetPeriod.WEEKLY:
            iso = dt.isocalendar()
            return f"PERIOD#{iso.year}-W{iso.week:02d}"
        else:  # MONTHLY
            return f"PERIOD#{dt.strftime('%Y-%m')}"


@dataclass
class UsageRecord:
    input_tokens: int
    output_tokens: int
    cost_usd: float


@dataclass
class BudgetSnapshot:
    """Current usage for a budget key + period."""
    key: BudgetKey
    period: BudgetPeriod
    tokens_in: int
    tokens_out: int
    cost_usd: float

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out


_TABLE_ENV = "BUDGET_TABLE"
_DEFAULT_TABLE = "ai-token-budgets"
_TTL_DAYS = 90


def _ttl_epoch(days: int = _TTL_DAYS) -> int:
    return int(time.time()) + days * 86400


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class BudgetCounters:
    """Atomic DynamoDB counters for token + cost budgets.

    All writes use ADD expressions so concurrent invocations are safe
    without optimistic concurrency control.
    """

    def __init__(
        self,
        table_name: str = _DEFAULT_TABLE,
        dynamodb_resource=None,
        region: Optional[str] = None,
    ):
        import boto3
        self._table_name = table_name
        if dynamodb_resource is None:
            dynamodb_resource = boto3.resource("dynamodb", region_name=region)
        self._table = dynamodb_resource.Table(table_name)

    def increment(
        self,
        key: BudgetKey,
        usage: UsageRecord,
        periods: list[BudgetPeriod] = None,
        now: Optional[datetime] = None,
    ) -> None:
        """Atomically add usage to all specified periods."""
        if periods is None:
            periods = [BudgetPeriod.MONTHLY]

        cost_micro = int(usage.cost_usd * 1_000_000)

        for period in periods:
            sk = key.sk(period, now)
            self._table.update_item(
                Key={"pk": key.pk, "sk": sk},
                UpdateExpression=(
                    "ADD tokens_in :ti, tokens_out :to, cost_usd_micro :cu "
                    "SET updated_at = :ua, #ttl = :ttl"
                ),
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":ti": usage.input_tokens,
                    ":to": usage.output_tokens,
                    ":cu": cost_micro,
                    ":ua": _now_iso(),
                    ":ttl": _ttl_epoch(),
                },
            )

    def get(
        self,
        key: BudgetKey,
        period: BudgetPeriod = BudgetPeriod.MONTHLY,
        now: Optional[datetime] = None,
    ) -> BudgetSnapshot:
        """Read current usage for key+period. Returns zeros if no record yet."""
        sk = key.sk(period, now)
        resp = self._table.get_item(
            Key={"pk": key.pk, "sk": sk},
            ConsistentRead=True,
        )
        item = resp.get("Item", {})
        return BudgetSnapshot(
            key=key,
            period=period,
            tokens_in=int(item.get("tokens_in", 0)),
            tokens_out=int(item.get("tokens_out", 0)),
            cost_usd=int(item.get("cost_usd_micro", 0)) / 1_000_000,
        )

    def reset(
        self,
        key: BudgetKey,
        period: BudgetPeriod = BudgetPeriod.MONTHLY,
        now: Optional[datetime] = None,
    ) -> None:
        """Delete the counter record (e.g. after manual limit reset by admin)."""
        sk = key.sk(period, now)
        self._table.delete_item(Key={"pk": key.pk, "sk": sk})
