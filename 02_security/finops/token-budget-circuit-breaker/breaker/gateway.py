"""BudgetGateway: drop-in wrapper around bedrock-runtime.converse().

Intercepts every call, checks budget state pre-call, records usage
post-call, and either passes through, throttles to a cheaper model,
or returns a structured block response — all transparent to the caller.

Pricing table is the same as agent-run-cost-tracer for consistency.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .budget import BudgetKey, UsageRecord
from .enforcer import BudgetEnforcer, BudgetState, EnforcerDecision

logger = logging.getLogger(__name__)

_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # Claude 4.x — current generation
    "anthropic.claude-haiku-4-5":  (0.001, 0.005),
    "anthropic.claude-sonnet-4-6": (0.003, 0.015),
    "anthropic.claude-sonnet-4":   (0.003, 0.015),
    "anthropic.claude-opus-4-7":   (0.015, 0.075),
    "anthropic.claude-opus-4":     (0.015, 0.075),
    # Claude 3.x — legacy, kept for older logs
    "anthropic.claude-3-5-sonnet": (0.003, 0.015),
    "anthropic.claude-3-5-haiku":  (0.0008, 0.004),
    "anthropic.claude-3-sonnet":   (0.003, 0.015),
    "anthropic.claude-3-haiku":    (0.00025, 0.00125),
    "anthropic.claude-3-opus":     (0.015, 0.075),
    "amazon.nova-pro":             (0.0008, 0.0032),
    "amazon.nova-lite":            (0.00006, 0.00024),
    "amazon.nova-micro":           (0.000035, 0.00014),
    "amazon.titan-text-express":   (0.0002, 0.0006),
    "amazon.titan-embed":          (0.00002, 0.0),
    "meta.llama3":                 (0.00065, 0.00065),
    "cohere.command-r-plus":       (0.003, 0.015),
    "cohere.command-r":            (0.0005, 0.0015),
}


def _estimate_cost(model_id: str, input_t: int, output_t: int) -> float:
    import re
    ml = re.sub(r"^(us|eu|ap)\.", "", model_id.lower())
    best = ""
    for k in _DEFAULT_PRICING:
        if ml.startswith(k.lower()) and len(k) > len(best):
            best = k
    inp, out = _DEFAULT_PRICING.get(best, (0.0, 0.0))
    return round((input_t / 1000.0) * inp + (output_t / 1000.0) * out, 8)


# Synthetic Bedrock response returned when budget is BLOCKED
def _blocked_response(message: str) -> dict:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": message}],
            }
        },
        "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
        "stopReason": "budget_limit_reached",
        "metrics": {},
        "$budget_blocked": True,
    }


@dataclass
class GatewayResult:
    response: dict
    decision: EnforcerDecision
    model_id_used: str
    model_id_requested: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float

    @property
    def was_blocked(self) -> bool:
        return self.decision.state == BudgetState.BLOCKED

    @property
    def was_throttled(self) -> bool:
        return self.decision.state == BudgetState.THROTTLED


class BudgetGateway:
    """Drop-in wrapper around a bedrock-runtime client.

    Usage::

        import boto3
        from breaker import BudgetGateway, BudgetKey

        bedrock = boto3.client("bedrock-runtime")
        gateway = BudgetGateway(bedrock_client=bedrock, table_name="ai-token-budgets")

        result = gateway.converse(
            budget_key=BudgetKey(tenant_id="acme", user_id="alice"),
            modelId="us.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        )
        # result.response is a standard bedrock-runtime converse() response
        # If blocked: result.was_blocked is True, response contains the limit message
        # If throttled: result.was_throttled is True, a cheaper model was used
    """

    def __init__(
        self,
        bedrock_client,
        table_name: str = "ai-token-budgets",
        dynamodb_resource=None,
        sns_client=None,
        pricing_overrides: Optional[dict] = None,
        region: Optional[str] = None,
    ):
        self._bedrock = bedrock_client
        self._enforcer = BudgetEnforcer(
            table_name=table_name,
            dynamodb_resource=dynamodb_resource,
            sns_client=sns_client,
            region=region,
        )
        self._pricing = {**_DEFAULT_PRICING, **(pricing_overrides or {})}

    def converse(
        self,
        budget_key: BudgetKey,
        now: Optional[datetime] = None,
        **kwargs,
    ) -> GatewayResult:
        """Check budget, invoke Bedrock, record usage. Returns GatewayResult."""
        requested_model = kwargs.get("modelId", "")

        # Pre-call budget check
        decision = self._enforcer.check(budget_key, now)

        if decision.should_block:
            logger.info("Budget BLOCKED: %s", budget_key.pk)
            return GatewayResult(
                response=_blocked_response(decision.block_message),
                decision=decision,
                model_id_used=requested_model,
                model_id_requested=requested_model,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=0.0,
            )

        # Apply throttle override: swap to cheaper model
        effective_model = requested_model
        if decision.should_throttle and decision.override_model_id:
            effective_model = decision.override_model_id
            logger.info(
                "Budget THROTTLED: %s → routing %s → %s",
                budget_key.pk, requested_model, effective_model,
            )
            kwargs = {**kwargs, "modelId": effective_model}

        # Invoke Bedrock
        resp = self._bedrock.converse(**kwargs)
        usage = resp.get("usage", {})
        input_t = usage.get("inputTokens", 0)
        output_t = usage.get("outputTokens", 0)
        cost = _estimate_cost(effective_model, input_t, output_t)

        # Post-call: record usage and re-evaluate (triggers SNS if threshold crossed)
        post_decision = self._enforcer.record(
            budget_key,
            UsageRecord(input_tokens=input_t, output_tokens=output_t, cost_usd=cost),
            now,
        )

        return GatewayResult(
            response=resp,
            decision=post_decision,
            model_id_used=effective_model,
            model_id_requested=requested_model,
            input_tokens=input_t,
            output_tokens=output_t,
            estimated_cost_usd=cost,
        )
