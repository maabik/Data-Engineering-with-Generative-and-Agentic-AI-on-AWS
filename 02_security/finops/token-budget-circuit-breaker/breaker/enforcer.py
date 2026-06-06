"""Budget enforcement logic: state machine + throttle/block decisions.

Budget states (per tenant/user/period):
  OK          usage < throttle_pct of limit
  THROTTLED   throttle_pct <= usage < 100% → switch to cheaper model
  BLOCKED     usage >= 100% → return graceful limit-reached message
  UNKNOWN     limit not configured → pass through (fail open)

State is evaluated at request time by reading DynamoDB (consistent read).
The gateway calls check() BEFORE the Bedrock call, then records() AFTER.

Limit config is stored in the same DynamoDB table under a special SK:
  SK = "LIMIT"

Limit item attributes:
  token_limit       INT    — max total tokens per period (0 = unlimited)
  cost_limit_usd    FLOAT  — max spend per period (0 = unlimited)
  throttle_pct      FLOAT  — 0.0–1.0; default 0.80
  throttle_model_id STR    — cheaper model to use when THROTTLED
  period            STR    — "monthly" | "daily" | "weekly"
  admin_contact     STR    — shown in BLOCKED response
  notification_arn  STR    — SNS topic ARN for threshold alerts
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .budget import BudgetCounters, BudgetKey, BudgetPeriod, BudgetSnapshot, UsageRecord

logger = logging.getLogger(__name__)


class BudgetState(str, Enum):
    OK = "ok"
    THROTTLED = "throttled"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


@dataclass
class BudgetLimit:
    token_limit: int = 0              # 0 = no limit
    cost_limit_usd: float = 0.0       # 0 = no limit
    throttle_pct: float = 0.80
    throttle_model_id: str = ""
    period: BudgetPeriod = BudgetPeriod.MONTHLY
    admin_contact: str = ""
    notification_arn: str = ""


@dataclass
class EnforcerDecision:
    state: BudgetState
    key: BudgetKey
    snapshot: Optional[BudgetSnapshot] = None
    limit: Optional[BudgetLimit] = None
    # If THROTTLED: override model_id to use instead
    override_model_id: str = ""
    # If BLOCKED: message to return to caller
    block_message: str = ""
    # % of limit consumed (for observability)
    token_pct: float = 0.0
    cost_pct: float = 0.0

    @property
    def should_block(self) -> bool:
        return self.state == BudgetState.BLOCKED

    @property
    def should_throttle(self) -> bool:
        return self.state == BudgetState.THROTTLED


_BLOCKED_MSG_TEMPLATE = (
    "Your {scope} has reached its {period} usage limit. "
    "No further requests will be processed until the limit resets or is increased. "
    "{contact_line}"
    "Reference: budget_key={budget_key}"
)

_DEFAULT_TABLE = "ai-token-budgets"


def _scope_label(key: BudgetKey) -> str:
    if key.agent_id:
        return f"agent '{key.agent_id}'"
    if key.user_id:
        return f"account '{key.user_id}'"
    return f"tenant '{key.tenant_id}'"


class BudgetEnforcer:
    """Evaluates budget state and records usage after each Bedrock call.

    Usage::

        enforcer = BudgetEnforcer(table_name="ai-token-budgets")

        # Before the Bedrock call:
        decision = enforcer.check(BudgetKey(tenant_id="acme", user_id="alice"))
        if decision.should_block:
            return {"error": decision.block_message}
        model_id = decision.override_model_id or original_model_id

        # After the Bedrock call:
        enforcer.record(
            BudgetKey(tenant_id="acme", user_id="alice"),
            UsageRecord(input_tokens=500, output_tokens=200, cost_usd=0.005),
        )
    """

    def __init__(
        self,
        table_name: str = _DEFAULT_TABLE,
        dynamodb_resource=None,
        sns_client=None,
        region: Optional[str] = None,
    ):
        self._counters = BudgetCounters(table_name, dynamodb_resource, region)
        self._table_name = table_name
        self._sns = sns_client
        self._region = region

    # ── Public API ─────────────────────────────────────────────────────────────

    def check(
        self,
        key: BudgetKey,
        now: Optional[datetime] = None,
    ) -> EnforcerDecision:
        """Read current usage and return enforcement decision."""
        limit = self._get_limit(key)
        if limit is None:
            return EnforcerDecision(state=BudgetState.UNKNOWN, key=key)

        snapshot = self._counters.get(key, limit.period, now)
        return self._evaluate(key, snapshot, limit)

    def record(
        self,
        key: BudgetKey,
        usage: UsageRecord,
        now: Optional[datetime] = None,
    ) -> EnforcerDecision:
        """Record usage AFTER a call and return updated state (for alerting)."""
        limit = self._get_limit(key)
        periods = [limit.period] if limit else [BudgetPeriod.MONTHLY]
        self._counters.increment(key, usage, periods, now)

        if limit is None:
            return EnforcerDecision(state=BudgetState.UNKNOWN, key=key)

        snapshot = self._counters.get(key, limit.period, now)
        decision = self._evaluate(key, snapshot, limit)
        self._maybe_notify(key, decision, limit)
        return decision

    def set_limit(
        self,
        key: BudgetKey,
        limit: BudgetLimit,
    ) -> None:
        """Write or overwrite the budget limit config for a key."""
        import boto3
        resource = self._counters._table._dynamodb_resource if hasattr(
            self._counters._table, "_dynamodb_resource"
        ) else boto3.resource("dynamodb", region_name=self._region)
        table = resource.Table(self._table_name)
        table.put_item(Item={
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

    # ── Private helpers ────────────────────────────────────────────────────────

    def _get_limit(self, key: BudgetKey) -> Optional[BudgetLimit]:
        """Read limit config from DynamoDB. Returns None if not configured."""
        try:
            resp = self._counters._table.get_item(
                Key={"pk": key.pk, "sk": "LIMIT"},
                ConsistentRead=True,
            )
            item = resp.get("Item")
            if not item:
                return None
            return BudgetLimit(
                token_limit=int(item.get("token_limit", 0)),
                cost_limit_usd=int(item.get("cost_limit_usd_micro", 0)) / 1_000_000,
                throttle_pct=float(item.get("throttle_pct", 0.80)),
                throttle_model_id=item.get("throttle_model_id", ""),
                period=BudgetPeriod(item.get("period", "monthly")),
                admin_contact=item.get("admin_contact", ""),
                notification_arn=item.get("notification_arn", ""),
            )
        except Exception as e:
            logger.error("Failed to read limit for %s: %s", key.pk, e)
            return None

    def _evaluate(
        self,
        key: BudgetKey,
        snapshot: BudgetSnapshot,
        limit: BudgetLimit,
    ) -> EnforcerDecision:
        token_pct = 0.0
        cost_pct = 0.0

        if limit.token_limit > 0:
            token_pct = snapshot.total_tokens / limit.token_limit
        if limit.cost_limit_usd > 0:
            cost_pct = snapshot.cost_usd / limit.cost_limit_usd

        usage_pct = max(token_pct, cost_pct)

        if usage_pct >= 1.0:
            contact_line = (f"Contact {limit.admin_contact} to increase your limit. "
                            if limit.admin_contact else "")
            msg = _BLOCKED_MSG_TEMPLATE.format(
                scope=_scope_label(key),
                period=limit.period.value,
                contact_line=contact_line,
                budget_key=key.pk,
            )
            return EnforcerDecision(
                state=BudgetState.BLOCKED,
                key=key,
                snapshot=snapshot,
                limit=limit,
                block_message=msg,
                token_pct=round(token_pct, 4),
                cost_pct=round(cost_pct, 4),
            )

        if usage_pct >= limit.throttle_pct:
            return EnforcerDecision(
                state=BudgetState.THROTTLED,
                key=key,
                snapshot=snapshot,
                limit=limit,
                override_model_id=limit.throttle_model_id,
                token_pct=round(token_pct, 4),
                cost_pct=round(cost_pct, 4),
            )

        return EnforcerDecision(
            state=BudgetState.OK,
            key=key,
            snapshot=snapshot,
            limit=limit,
            token_pct=round(token_pct, 4),
            cost_pct=round(cost_pct, 4),
        )

    def _maybe_notify(
        self,
        key: BudgetKey,
        decision: EnforcerDecision,
        limit: BudgetLimit,
    ) -> None:
        """Publish SNS notification at threshold crossings."""
        if not limit.notification_arn or self._sns is None:
            return
        if decision.state not in (BudgetState.THROTTLED, BudgetState.BLOCKED):
            return
        try:
            payload = {
                "budget_key": key.pk,
                "state": decision.state.value,
                "token_pct": decision.token_pct,
                "cost_pct": decision.cost_pct,
                "period": limit.period.value,
            }
            self._sns.publish(
                TopicArn=limit.notification_arn,
                Subject=f"Budget {decision.state.value.upper()}: {key.pk}",
                Message=json.dumps(payload),
                MessageAttributes={
                    "state": {"DataType": "String", "StringValue": decision.state.value},
                    "tenant_id": {"DataType": "String", "StringValue": key.tenant_id},
                },
            )
        except Exception as e:
            logger.warning("SNS notify failed for %s: %s", key.pk, e)
