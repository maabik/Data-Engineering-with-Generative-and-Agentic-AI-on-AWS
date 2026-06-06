"""Source (d): AgentCore Runtime usage records.

AgentCore Runtime logs agent execution metadata to CloudWatch Logs under
/aws/bedrock/agentcore/runtime/. Each log event is a JSON object describing
a completed agent run with token counts, tool call counts, and cost metadata.

Expected log event shape (AgentCore Runtime execution summary):
{
  "type": "EXECUTION_SUMMARY",
  "executionId": "...",
  "agentId": "...",
  "agentAliasId": "...",
  "agentName": "...",
  "sessionId": "...",
  "startTime": "2026-01-15T10:30:00Z",
  "endTime": "2026-01-15T10:30:05Z",
  "status": "SUCCEEDED",
  "totalInputTokens": 500,
  "totalOutputTokens": 200,
  "modelInvocations": 3,
  "toolInvocations": 2,
  "orchestrationModel": "anthropic.claude-3-sonnet-20240229-v1:0",
  "requestMetadata": {
    "user_id": "...",
    "tenant_id": "...",
    "trace_id": "..."
  },
  "costAllocationTags": {
    "Workload": "...",
    "Team": "..."
  }
}
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Generator

import boto3

from .bedrock_logs import DEFAULT_PRICING, _estimate_cost

logger = logging.getLogger(__name__)

LOG_GROUP_PREFIX = "/aws/bedrock/agentcore/runtime"


def _iter_log_events(
    log_group: str,
    start_time_ms: int,
    end_time_ms: int,
    cw_client,
) -> Generator[dict, None, None]:
    kwargs = {
        "logGroupName": log_group,
        "startTime": start_time_ms,
        "endTime": end_time_ms,
        "filterPattern": '{ $.type = "EXECUTION_SUMMARY" }',
        "interleaved": True,
    }
    paginator = cw_client.get_paginator("filter_log_events")
    for page in paginator.paginate(**kwargs):
        for event in page.get("events", []):
            try:
                yield json.loads(event["message"])
            except (json.JSONDecodeError, KeyError):
                continue


def _list_log_groups(cw_client, prefix: str) -> list[str]:
    groups = []
    paginator = cw_client.get_paginator("describe_log_groups")
    for page in paginator.paginate(logGroupNamePrefix=prefix):
        for g in page.get("logGroups", []):
            groups.append(g["logGroupName"])
    return groups


def extract(
    start_date: date,
    end_date: date,
    log_group_prefix: str = LOG_GROUP_PREFIX,
    pricing_overrides: dict | None = None,
    cw_client=None,
    account_id: str = "",
    region: str = "",
) -> Generator[dict, None, None]:
    """Yield normalised FinOps records from AgentCore Runtime CloudWatch logs."""
    if cw_client is None:
        cw_client = boto3.client("logs")
    pricing = {**DEFAULT_PRICING, **(pricing_overrides or {})}

    start_ms = int(datetime(start_date.year, start_date.month, start_date.day,
                             tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59,
                           tzinfo=timezone.utc).timestamp() * 1000)

    log_groups = _list_log_groups(cw_client, log_group_prefix)
    if not log_groups:
        # Fallback: try the exact prefix as a log group name
        log_groups = [log_group_prefix]

    for log_group in log_groups:
        try:
            for event in _iter_log_events(log_group, start_ms, end_ms, cw_client):
                model_id = event.get("orchestrationModel", "")
                input_tokens = int(event.get("totalInputTokens") or 0)
                output_tokens = int(event.get("totalOutputTokens") or 0)

                ts = event.get("startTime", "")
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    dt = datetime.now(timezone.utc)

                metadata = event.get("requestMetadata") or {}
                tags = event.get("costAllocationTags") or {}

                cost_usd = _estimate_cost(input_tokens, output_tokens, model_id, pricing)

                yield {
                    "source": "agentcore_runtime",
                    "event_time": dt.isoformat(),
                    "date": dt.date().isoformat(),
                    "account_id": account_id,
                    "region": region,
                    "model_id": model_id,
                    "model_family": ".".join(model_id.split(".")[:2]).split(":")[0] if model_id else "",
                    "tenant_id": metadata.get("tenant_id") or tags.get("TenantId", "_unknown"),
                    "user_id": metadata.get("user_id", ""),
                    "session_id": event.get("sessionId", "") or metadata.get("session_id", ""),
                    "trace_id": metadata.get("trace_id", ""),
                    "workload": tags.get("Workload", ""),
                    "team": tags.get("Team", ""),
                    "request_id": event.get("executionId", ""),
                    "agent_id": event.get("agentId", ""),
                    "agent_alias_id": event.get("agentAliasId", ""),
                    "agent_name": event.get("agentName", ""),
                    "execution_status": event.get("status", ""),
                    "model_invocations": int(event.get("modelInvocations") or 0),
                    "tool_invocations": int(event.get("toolInvocations") or 0),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "latency_ms": int(
                        (datetime.fromisoformat((event.get("endTime") or ts).replace("Z", "+00:00"))
                         - dt).total_seconds() * 1000
                    ) if event.get("endTime") else 0,
                    "estimated_cost_usd": cost_usd,
                    "cur_line_item_id": "",
                    "inference_profile_id": "",
                    "inference_profile_name": "",
                    "guardrail_action": "",
                    "injection_detected": False,
                    "pii_detected": False,
                    "error_code": "" if event.get("status") == "SUCCEEDED" else event.get("status", ""),
                }
        except Exception as e:
            logger.error("Failed reading log group %s: %s", log_group, e)
