"""Source (a): Bedrock invocation log records from the audit-trail S3 bucket.

Reads Firehose-landed NDJSON files, normalises each record to the shared
FinOps schema, and yields InvocationRecord dicts ready for the Iceberg writer.

Expected input shape matches the bedrock-audit-trail transform Lambda output:
{
  "event_time": "2026-01-15T10:30:00+00:00",
  "request_id": "...",
  "account_id": "...",
  "region": "...",
  "model_id": "anthropic.claude-3-sonnet-20240229-v1:0",
  "user_id": "...",
  "tenant_id": "...",
  "session_id": "...",
  "trace_id": "...",
  "input_tokens": 100,
  "output_tokens": 50,
  "latency_ms": 980,
  "guardrail_action": "NONE",
  "injection_detected": false,
  "pii_detected": false,
  "error_code": "",
  ...
}
"""

from __future__ import annotations

import gzip
import io
import json
import logging
from datetime import date, datetime, timezone
from typing import Generator

import boto3

logger = logging.getLogger(__name__)

# On-demand pricing table (USD per 1K tokens) as of early 2026.
# Keys match the prefix of model_id. Longest prefix wins.
# Operators can override by passing a pricing_overrides dict.
DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    # (input $/1K tokens, output $/1K tokens)
    "anthropic.claude-3-5-sonnet": (0.003, 0.015),
    "anthropic.claude-3-5-haiku":  (0.0008, 0.004),
    "anthropic.claude-3-sonnet":   (0.003, 0.015),
    "anthropic.claude-3-haiku":    (0.00025, 0.00125),
    "anthropic.claude-3-opus":     (0.015, 0.075),
    "amazon.nova-pro":             (0.0008, 0.0032),
    "amazon.nova-lite":            (0.00006, 0.00024),
    "amazon.nova-micro":           (0.000035, 0.00014),
    "amazon.titan-text-express":   (0.0002, 0.0006),
    "amazon.titan-text-lite":      (0.00015, 0.0002),
    "meta.llama3":                 (0.00065, 0.00065),
    "mistral.mistral-large":       (0.004, 0.012),
    "mistral.mistral-7b":          (0.00015, 0.0002),
    "cohere.command-r-plus":       (0.003, 0.015),
    "cohere.command-r":            (0.0005, 0.0015),
}


def _lookup_price(model_id: str, pricing: dict[str, tuple[float, float]]) -> tuple[float, float]:
    """Return (input_price_per_1k, output_price_per_1k) for a model_id."""
    model_id_lower = model_id.lower()
    # longest-prefix match
    best_key = ""
    for key in pricing:
        if model_id_lower.startswith(key.lower()) and len(key) > len(best_key):
            best_key = key
    if best_key:
        return pricing[best_key]
    return (0.0, 0.0)


def _estimate_cost(input_tokens: int, output_tokens: int, model_id: str,
                   pricing: dict) -> float:
    inp, out = _lookup_price(model_id, pricing)
    return round((input_tokens / 1000.0) * inp + (output_tokens / 1000.0) * out, 8)


def _parse_event_time(ts: str) -> tuple[datetime, date]:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.now(timezone.utc)
    return dt, dt.date()


def _iter_s3_prefix(bucket: str, prefix: str, s3_client) -> Generator[dict, None, None]:
    """Yield parsed records from all NDJSON(.gz) objects under a prefix."""
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            try:
                body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
                if key.endswith(".gz"):
                    body = gzip.decompress(body)
                for line in body.decode("utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError as e:
                        logger.warning("Bad JSON in %s: %s", key, e)
            except Exception as e:
                logger.error("Failed reading %s: %s", key, e)


def extract(
    bucket: str,
    prefix: str = "",
    pricing_overrides: dict | None = None,
    s3_client=None,
) -> Generator[dict, None, None]:
    """Yield normalised FinOps records from the Bedrock audit-trail S3 bucket."""
    if s3_client is None:
        s3_client = boto3.client("s3")
    pricing = {**DEFAULT_PRICING, **(pricing_overrides or {})}

    for raw in _iter_s3_prefix(bucket, prefix, s3_client):
        model_id = raw.get("model_id", "")
        input_tokens = int(raw.get("input_tokens") or 0)
        output_tokens = int(raw.get("output_tokens") or 0)
        dt, day = _parse_event_time(raw.get("event_time", ""))

        cost_usd = _estimate_cost(input_tokens, output_tokens, model_id, pricing)
        inp_price, out_price = _lookup_price(model_id, pricing)

        yield {
            "source": "bedrock_invocation",
            "event_time": dt.isoformat(),
            "date": day.isoformat(),
            "account_id": raw.get("account_id", ""),
            "region": raw.get("region", ""),
            "model_id": model_id,
            "model_family": ".".join(model_id.split(".")[:2]).split(":")[0] if model_id else "",
            "tenant_id": raw.get("tenant_id") or "_unknown",
            "user_id": raw.get("user_id", ""),
            "session_id": raw.get("session_id", ""),
            "trace_id": raw.get("trace_id", ""),
            "workload": raw.get("workload", ""),
            "request_id": raw.get("request_id", ""),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "latency_ms": int(raw.get("latency_ms") or 0),
            "estimated_cost_usd": cost_usd,
            "input_price_per_1k": inp_price,
            "output_price_per_1k": out_price,
            "guardrail_action": raw.get("guardrail_action", "NONE"),
            "injection_detected": bool(raw.get("injection_detected", False)),
            "pii_detected": bool(raw.get("pii_detected", False)),
            "error_code": raw.get("error_code", ""),
            # CUR join key — populated by the join stage
            "cur_line_item_id": "",
            # Inference profile — populated by enrich stage
            "inference_profile_id": "",
            "inference_profile_name": "",
        }
