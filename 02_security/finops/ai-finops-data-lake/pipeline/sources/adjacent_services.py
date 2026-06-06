"""Source (e): Adjacent AI service costs — Knowledge Base, OpenSearch, S3 Vectors.

These services back Bedrock Knowledge Bases and vector search but their costs
appear under different product codes in CUR. This module pulls them from CUR
records already extracted by the CUR source, filters to AI-adjacent service
codes, and attaches a service_category label so cost dashboards can break out:
  - bedrock_kb      : Knowledge Base management fees
  - opensearch      : OpenSearch Serverless (vector search backing KB)
  - s3_vectors      : S3 Vectors bucket storage and request costs
  - bedrock_guardrail: Guardrail evaluation charges

Alternatively, when a dedicated CUR prefix is not available, this source can
query Cost Explorer directly for a date range.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Generator

import boto3

logger = logging.getLogger(__name__)

# Maps CUR product_code → service_category label
SERVICE_CATEGORY_MAP: dict[str, str] = {
    "AmazonBedrock": "bedrock",
    "AmazonBedrockAgentCore": "agentcore",
    "AmazonOpenSearchService": "opensearch",
    "AmazonOpenSearchServerless": "opensearch",
    "AmazonS3": "s3",
}

# Usage type fragments that indicate AI-adjacent S3 costs (S3 Vectors, KB backing)
AI_S3_USAGE_FRAGMENTS = ["VectorBucket", "KnowledgeBase", "BedrockKB"]

# Bedrock usage type fragments that map to specific sub-services
KB_USAGE_FRAGMENTS = ["KnowledgeBase", "Retrieval", "Ingestion", "SyncJob"]
GUARDRAIL_USAGE_FRAGMENTS = ["Guardrail"]


def _service_category(product_code: str, usage_type: str) -> str:
    """Derive a fine-grained service category from product_code + usage_type."""
    if product_code == "AmazonBedrock":
        usage_upper = usage_type.upper()
        if any(f.upper() in usage_upper for f in KB_USAGE_FRAGMENTS):
            return "bedrock_kb"
        if any(f.upper() in usage_upper for f in GUARDRAIL_USAGE_FRAGMENTS):
            return "bedrock_guardrail"
        return "bedrock"
    if product_code in ("AmazonOpenSearchService", "AmazonOpenSearchServerless"):
        return "opensearch"
    if product_code == "AmazonS3":
        if any(f.upper() in usage_type.upper() for f in AI_S3_USAGE_FRAGMENTS):
            return "s3_vectors"
        return "s3"
    return SERVICE_CATEGORY_MAP.get(product_code, product_code.lower())


def filter_from_cur_records(cur_records: list[dict]) -> Generator[dict, None, None]:
    """Re-label CUR records with service_category. No additional API calls."""
    ai_services = set(SERVICE_CATEGORY_MAP.keys())
    for rec in cur_records:
        pc = rec.get("product_code", "")
        if pc not in ai_services:
            continue
        cat = _service_category(pc, rec.get("usage_type", ""))
        yield {**rec, "service_category": cat, "source": f"cur_{cat}"}


def extract_from_cost_explorer(
    start_date: date,
    end_date: date,
    granularity: str = "DAILY",
    group_by_tags: list[str] | None = None,
    ce_client=None,
    account_id: str = "",
    region: str = "",
) -> Generator[dict, None, None]:
    """Fallback: pull AI-adjacent costs from Cost Explorer when CUR is not configured.

    Returns one record per (date, service, usage_type, tag group).
    Less granular than CUR but needs no S3 prefix setup.
    """
    if ce_client is None:
        ce_client = boto3.client("ce")

    group_by = [{"Type": "DIMENSION", "Key": "SERVICE"}]
    if group_by_tags:
        group_by += [{"Type": "TAG", "Key": t} for t in group_by_tags[:3]]  # CE limit: 4 groups

    filters = {
        "Dimensions": {
            "Key": "SERVICE",
            "Values": list(SERVICE_CATEGORY_MAP.keys()),
            "MatchOptions": ["EQUALS"],
        }
    }

    kwargs = {
        "TimePeriod": {
            "Start": start_date.isoformat(),
            "End": (end_date + timedelta(days=1)).isoformat(),
        },
        "Granularity": granularity,
        "Filter": filters,
        "GroupBy": group_by,
        "Metrics": ["UnblendedCost", "UsageQuantity"],
    }

    try:
        paginator = ce_client.get_paginator("get_cost_and_usage")
    except AttributeError:
        # CE does not have a paginator — use manual pagination
        token = None
        while True:
            if token:
                kwargs["NextPageToken"] = token
            resp = ce_client.get_cost_and_usage(**kwargs)
            for result in resp.get("ResultsByTime", []):
                period_start = result["TimePeriod"]["Start"]
                for group in result.get("Groups", []):
                    keys = group.get("Keys", [])
                    service = keys[0] if keys else ""
                    tag_vals = keys[1:] if len(keys) > 1 else []
                    cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
                    usage = float(group["Metrics"]["UsageQuantity"]["Amount"])
                    cat = _service_category(service, "")

                    tenant_id = tag_vals[0].split("$")[-1] if tag_vals else "_unknown"
                    yield {
                        "source": f"ce_{cat}",
                        "service_category": cat,
                        "event_time": period_start + "T00:00:00+00:00",
                        "date": period_start,
                        "account_id": account_id,
                        "region": region,
                        "model_id": "",
                        "model_family": "",
                        "tenant_id": tenant_id or "_unknown",
                        "user_id": "",
                        "session_id": "",
                        "trace_id": "",
                        "workload": "",
                        "request_id": "",
                        "product_code": service,
                        "usage_type": service,
                        "usage_amount": usage,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "latency_ms": 0,
                        "estimated_cost_usd": cost,
                        "actual_cost_usd": cost,
                        "iam_principal": "",
                        "cur_line_item_id": "",
                        "inference_profile_id": "",
                        "inference_profile_name": "",
                        "guardrail_action": "",
                        "injection_detected": False,
                        "pii_detected": False,
                        "error_code": "",
                    }
            token = resp.get("NextPageToken")
            if not token:
                break
        return

    for page in paginator.paginate(**kwargs):
        for result in page.get("ResultsByTime", []):
            period_start = result["TimePeriod"]["Start"]
            for group in result.get("Groups", []):
                keys = group.get("Keys", [])
                service = keys[0] if keys else ""
                cost = float(group["Metrics"]["UnblendedCost"]["Amount"])
                usage = float(group["Metrics"]["UsageQuantity"]["Amount"])
                cat = _service_category(service, "")
                yield {
                    "source": f"ce_{cat}",
                    "service_category": cat,
                    "event_time": period_start + "T00:00:00+00:00",
                    "date": period_start,
                    "account_id": account_id,
                    "region": region,
                    "model_id": "",
                    "model_family": "",
                    "tenant_id": "_unknown",
                    "user_id": "",
                    "session_id": "",
                    "trace_id": "",
                    "workload": "",
                    "request_id": "",
                    "product_code": service,
                    "usage_type": service,
                    "usage_amount": usage,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "latency_ms": 0,
                    "estimated_cost_usd": cost,
                    "actual_cost_usd": cost,
                    "iam_principal": "",
                    "cur_line_item_id": "",
                    "inference_profile_id": "",
                    "inference_profile_name": "",
                    "guardrail_action": "",
                    "injection_detected": False,
                    "pii_detected": False,
                    "error_code": "",
                }
