"""Lambda: records Bedrock usage into BudgetCounters after each invocation.

Triggered by EventBridge rules that match Bedrock invocation log events
(delivered via CloudTrail → EventBridge). Also handles direct invocation
from BudgetGateway for synchronous recording (when the library is
not available in the caller's runtime).

Event sources
-------------
1. EventBridge / CloudTrail Bedrock events:
   {
     "source": "aws.bedrock",
     "detail": {
       "requestParameters": {"modelId": "...", "requestMetadata": {...}},
       "responseElements": {"usage": {"inputTokens": N, "outputTokens": N}},
       "userIdentity": {"arn": "..."}
     }
   }

2. Direct Lambda invoke (synchronous, from BudgetGateway):
   {
     "source": "budget_gateway",
     "tenant_id": "acme",
     "user_id": "alice",
     "agent_id": null,
     "model_id": "us.anthropic.claude-sonnet-4-6",
     "input_tokens": 500,
     "output_tokens": 200,
     "cost_usd": 0.005
   }
"""

from __future__ import annotations

import json
import logging
import os
import sys

# Allow importing breaker package when bundled alongside this handler
sys.path.insert(0, os.path.dirname(__file__))

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_TABLE = os.environ.get("BUDGET_TABLE", "ai-token-budgets")
_ALERT_ARN = os.environ.get("ALERT_TOPIC_ARN", "")

_ddb = None
_sns = None


def _get_ddb():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb")
    return _ddb


def _get_sns():
    global _sns
    if _sns is None:
        _sns = boto3.client("sns")
    return _sns


def _parse_cloudtrail_event(detail: dict) -> dict | None:
    """Extract usage fields from a CloudTrail Bedrock event."""
    params = detail.get("requestParameters", {})
    resp_elements = detail.get("responseElements", {})
    metadata = params.get("requestMetadata", {})

    model_id = params.get("modelId", "")
    usage = resp_elements.get("usage", {})
    input_tokens = int(usage.get("inputTokens", 0))
    output_tokens = int(usage.get("outputTokens", 0))

    if not model_id or (input_tokens == 0 and output_tokens == 0):
        return None

    return {
        "tenant_id": metadata.get("tenant_id", ""),
        "user_id": metadata.get("user_id", ""),
        "agent_id": metadata.get("agent_id", ""),
        "model_id": model_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def _estimate_cost(model_id: str, input_t: int, output_t: int) -> float:
    _PRICING = {
        # Claude 4.x — current generation
        "anthropic.claude-haiku-4-5":  (0.001, 0.005),
        "anthropic.claude-sonnet-4-6": (0.003, 0.015),
        "anthropic.claude-sonnet-4":   (0.003, 0.015),
        "anthropic.claude-opus-4-7":   (0.015, 0.075),
        "anthropic.claude-opus-4":     (0.015, 0.075),
        # Claude 3.x — legacy
        "anthropic.claude-3-5-sonnet": (0.003, 0.015),
        "anthropic.claude-3-haiku":    (0.00025, 0.00125),
        "anthropic.claude-3-opus":     (0.015, 0.075),
        "amazon.nova-pro":             (0.0008, 0.0032),
        "amazon.nova-lite":            (0.00006, 0.00024),
        "amazon.nova-micro":           (0.000035, 0.00014),
        "meta.llama3":                 (0.00065, 0.00065),
    }
    ml = model_id.lower()
    best = ""
    for k in _PRICING:
        if ml.startswith(k) and len(k) > len(best):
            best = k
    inp, out = _PRICING.get(best, (0.0, 0.0))
    return round((input_t / 1000.0) * inp + (output_t / 1000.0) * out, 8)


def handler(event: dict, context) -> dict:
    source = event.get("source", "")

    # Direct invocation from BudgetGateway
    if source == "budget_gateway":
        usage_info = event
    elif source == "aws.bedrock":
        usage_info = _parse_cloudtrail_event(event.get("detail", {}))
        if not usage_info:
            logger.info("Skipping Bedrock event with no usage data")
            return {"statusCode": 200, "processed": False}
    else:
        logger.warning("Unknown event source: %s", source)
        return {"statusCode": 400, "error": "unknown source"}

    tenant_id = usage_info.get("tenant_id", "")
    user_id = usage_info.get("user_id", "")
    agent_id = usage_info.get("agent_id", "")
    model_id = usage_info.get("model_id", "")
    input_tokens = int(usage_info.get("input_tokens", 0))
    output_tokens = int(usage_info.get("output_tokens", 0))
    cost_usd = float(usage_info.get("cost_usd", 0.0)) or _estimate_cost(
        model_id, input_tokens, output_tokens
    )

    if not tenant_id:
        logger.info("No tenant_id in event — skipping budget record")
        return {"statusCode": 200, "processed": False}

    # Lazy import to allow local bundling
    from breaker.budget import BudgetCounters, BudgetKey, BudgetPeriod, UsageRecord
    from breaker.enforcer import BudgetEnforcer

    key = BudgetKey(tenant_id=tenant_id,
                    user_id=user_id or None,
                    agent_id=agent_id or None)
    usage = UsageRecord(input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        cost_usd=cost_usd)

    enforcer = BudgetEnforcer(
        table_name=_TABLE,
        dynamodb_resource=_get_ddb(),
        sns_client=_get_sns() if _ALERT_ARN else None,
    )
    decision = enforcer.record(key, usage)

    logger.info(
        "Recorded usage tenant=%s user=%s tokens=%d+%d cost=%.6f state=%s",
        tenant_id, user_id, input_tokens, output_tokens, cost_usd,
        decision.state.value,
    )

    return {
        "statusCode": 200,
        "processed": True,
        "state": decision.state.value,
        "token_pct": decision.token_pct,
        "cost_pct": decision.cost_pct,
    }
