"""Parse a raw Bedrock invocation log record into a structured dict.

Bedrock invocation logs have this top-level shape (schemaType=ModelInvocationLog):
{
  "schemaType": "ModelInvocationLog",
  "schemaVersion": "1.0",
  "timestamp": "2024-01-01T00:00:00Z",
  "accountId": "...",
  "identity": {"arn": "..."},
  "region": "us-east-1",
  "requestId": "...",
  "operation": "InvokeModel",
  "modelId": "anthropic.claude-3-sonnet-...",
  "errorCode": null,
  "errorMessage": null,
  "input": {
    "inputBodyJson": { ... },
    "inputTokenCount": 100,
    "inputContentType": "application/json"
  },
  "output": {
    "outputBodyJson": { ... },
    "outputTokenCount": 200,
    "outputContentType": "application/json"
  },
  "requestMetadata": {
    "user_id": "...",
    "tenant_id": "...",
    "session_id": "...",
    "trace_id": "..."
  },
  "performance": {
    "latency": 1234
  },
  "guardrailsTrace": { ... }  # present when a guardrail was invoked
}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _extract_text_from_body(body: dict | None, model_id: str) -> str:
    """Best-effort extraction of the prompt/response text from the model body."""
    if not body:
        return ""
    model_id = (model_id or "").lower()

    # Anthropic Messages API
    if "messages" in body:
        parts = []
        for msg in body.get("messages", []):
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
        # also capture system prompt
        if "system" in body:
            sys = body["system"]
            if isinstance(sys, str):
                parts.insert(0, sys)
            elif isinstance(sys, list):
                for block in sys:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.insert(0, block.get("text", ""))
        return "\n".join(parts)

    # Anthropic completions API
    if "prompt" in body:
        return body["prompt"]

    # Amazon Titan / Nova
    if "inputText" in body:
        return body["inputText"]
    if "results" in body:
        return " ".join(r.get("outputText", "") for r in body["results"])

    # Meta Llama
    if "prompt" in body:
        return body["prompt"]
    if "generation" in body:
        return body["generation"]

    # Cohere
    if "texts" in body:
        return " ".join(body["texts"])
    if "generations" in body:
        return " ".join(g.get("text", "") for g in body["generations"])

    # Mistral
    if "outputs" in body:
        return " ".join(o.get("text", "") for o in body["outputs"])

    return json.dumps(body)[:2000]


def _guardrail_summary(trace: dict | None) -> tuple[str, list[str], list[str], bool]:
    """Returns (action, topics_triggered, content_filters, injection_detected)."""
    if not trace:
        return "NONE", [], [], False

    action = trace.get("action", "NONE")
    topics: list[str] = []
    filters: list[str] = []
    injection = False

    outputs = trace.get("outputAssessments") or trace.get("inputAssessments") or {}
    for guardrail_id, assessments in outputs.items():
        if not isinstance(assessments, list):
            assessments = [assessments]
        for assessment in assessments:
            for topic in assessment.get("topicPolicy", {}).get("topics", []):
                if topic.get("action") in ("BLOCKED", "MASKED"):
                    topics.append(topic.get("name", "unknown"))
            for cf in assessment.get("contentPolicy", {}).get("filters", []):
                if cf.get("action") in ("BLOCKED", "MASKED"):
                    name = cf.get("type", "unknown")
                    filters.append(name)
                    if name.upper() == "PROMPT_ATTACK":
                        injection = True

    return action, list(set(topics)), list(set(filters)), injection


def parse_record(raw: dict) -> dict:
    """Convert a raw Bedrock log record to the audit trail schema."""
    schema = raw.get("schemaType", "")
    if schema not in ("ModelInvocationLog", "AgentInvocationLog", ""):
        return {}

    request_id = raw.get("requestId", "")
    model_id = raw.get("modelId", "")
    timestamp_str = raw.get("timestamp", "")

    try:
        event_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00")).isoformat()
    except Exception:
        event_time = datetime.now(timezone.utc).isoformat()

    input_block = raw.get("input") or {}
    output_block = raw.get("output") or {}
    metadata = raw.get("requestMetadata") or {}
    perf = raw.get("performance") or {}

    input_body = input_block.get("inputBodyJson") or {}
    output_body = output_block.get("outputBodyJson") or {}

    if isinstance(input_body, str):
        try:
            input_body = json.loads(input_body)
        except json.JSONDecodeError:
            input_body = {}
    if isinstance(output_body, str):
        try:
            output_body = json.loads(output_body)
        except json.JSONDecodeError:
            output_body = {}

    input_text = _extract_text_from_body(input_body, model_id)
    output_text = _extract_text_from_body(output_body, model_id)

    guardrail_action, topics, content_filters, injection = _guardrail_summary(
        raw.get("guardrailsTrace")
    )

    # Derive model_family partition value (e.g. "anthropic.claude-3-sonnet-20240229-v1")
    # Strip the :version suffix (e.g. ":0") that appears in Bedrock model IDs.
    model_family = ".".join(model_id.split(".")[:2]).split(":")[0] if model_id else "unknown"

    event_date = event_time[:10]  # YYYY-MM-DD

    return {
        "event_time": event_time,
        "request_id": request_id,
        "account_id": raw.get("accountId", ""),
        "region": raw.get("region", ""),
        "model_id": model_id,
        "model_family": model_family,
        "user_id": metadata.get("user_id", ""),
        "tenant_id": metadata.get("tenant_id", ""),
        "session_id": metadata.get("session_id", ""),
        "trace_id": metadata.get("trace_id", ""),
        "input_tokens": input_block.get("inputTokenCount", 0),
        "output_tokens": output_block.get("outputTokenCount", 0),
        "latency_ms": perf.get("latency", 0),
        # text fields will be replaced by PII-redacted versions in transform.py
        "_input_text_raw": input_text,
        "_output_text_raw": output_text,
        "input_text_redacted": input_text,
        "output_text_redacted": output_text,
        "pii_detected": False,
        "pii_entity_types": [],
        "pii_token_ids": [],
        "guardrail_action": guardrail_action,
        "guardrail_id": (raw.get("guardrailsTrace") or {}).get("guardrailId", ""),
        "guardrail_topics_triggered": topics,
        "guardrail_content_filters": content_filters,
        "injection_detected": injection,
        "sampled": True,
        "error_code": raw.get("errorCode") or "",
        "error_message": raw.get("errorMessage") or "",
        "date": event_date,
        "tenant_id_part": metadata.get("tenant_id", "_unknown"),
    }
