"""Source (b): AWS Cost and Usage Report 2.0 (CUR 2.0) Parquet exports.

CUR 2.0 is delivered to S3 as Parquet files under a date-partitioned prefix.
This module reads them with PyArrow (no Spark needed for reasonable report
sizes) and emits normalised FinOps records for Bedrock-related line items.

CUR 2.0 columns used:
  line_item_product_code      — filter: "AmazonBedrock"
  line_item_usage_account_id
  line_item_usage_start_date
  line_item_usage_type        — e.g. "USE1-Bedrock:InputTokens"
  line_item_usage_amount
  line_item_unblended_cost
  line_item_line_item_type    — filter: "Usage"
  line_item_iam_principal     — the IAM principal that made the call
  resource_tags_*             — cost-allocation tags (mapped to tenant/workload)
  product_region
  product_model_id            — Bedrock model ID (when present)

Tag mapping (configurable):
  resource_tags_user_tenant   → tenant_id
  resource_tags_user_workload → workload
  resource_tags_user_team     → team
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Generator

import boto3

logger = logging.getLogger(__name__)

# CUR 2.0 service code for Bedrock and adjacent AI services
BEDROCK_SERVICE_CODES = {
    "AmazonBedrock",
    "AmazonBedrockAgentCore",
    "AmazonOpenSearchService",
    "AmazonS3",  # S3 Vectors / Knowledge Base backing store
}

DEFAULT_TAG_MAP = {
    "tenant_id": ["resource_tags_user_tenant", "resource_tags_user_TenantId"],
    "workload": ["resource_tags_user_workload", "resource_tags_user_Workload"],
    "team": ["resource_tags_user_team", "resource_tags_user_Team"],
    "environment": ["resource_tags_user_environment", "resource_tags_user_Environment"],
}


def _resolve_tag(row: dict, candidates: list[str]) -> str:
    for col in candidates:
        val = row.get(col, "")
        if val and val != "$GENERIC":
            return str(val)
    return ""


def _parse_date(val) -> str:
    if val is None:
        return date.today().isoformat()
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    s = str(val)
    return s[:10] if len(s) >= 10 else s


def _iter_parquet_prefix(bucket: str, prefix: str, s3_client, filter_services: set[str]) -> Generator[dict, None, None]:
    """Yield row dicts from CUR 2.0 Parquet files under a prefix."""
    try:
        import pyarrow.parquet as pq
        import pyarrow as pa
    except ImportError:
        raise ImportError("pyarrow is required for CUR processing: pip install pyarrow")

    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.endswith(".parquet"):
                continue
            try:
                body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
                import io
                table = pq.read_table(io.BytesIO(body))
                columns = table.schema.names
                for batch in table.to_batches(max_chunksize=1000):
                    rows = batch.to_pydict()
                    n = batch.num_rows
                    for i in range(n):
                        row = {col: rows[col][i] for col in columns}
                        svc = str(row.get("line_item_product_code", ""))
                        if filter_services and svc not in filter_services:
                            continue
                        ltype = str(row.get("line_item_line_item_type", ""))
                        if ltype not in ("Usage", "SavingsPlanCoveredUsage"):
                            continue
                        yield row
            except Exception as e:
                logger.error("Failed reading CUR file %s: %s", key, e)


def extract(
    bucket: str,
    prefix: str = "",
    filter_services: set[str] | None = None,
    tag_map: dict[str, list[str]] | None = None,
    s3_client=None,
) -> Generator[dict, None, None]:
    """Yield normalised FinOps CUR records."""
    if s3_client is None:
        s3_client = boto3.client("s3")
    services = filter_services if filter_services is not None else BEDROCK_SERVICE_CODES
    tags = tag_map or DEFAULT_TAG_MAP

    for row in _iter_parquet_prefix(bucket, prefix, s3_client, services):
        product_code = str(row.get("line_item_product_code", ""))
        model_id = str(row.get("product_model_id", "") or "")
        usage_type = str(row.get("line_item_usage_type", ""))
        iam_principal = str(row.get("line_item_iam_principal", "") or "")

        cost = float(row.get("line_item_unblended_cost") or 0.0)
        usage_amount = float(row.get("line_item_usage_amount") or 0.0)

        # Derive input/output token counts from usage_type when possible
        input_tokens = 0
        output_tokens = 0
        if "InputToken" in usage_type or "InputChar" in usage_type:
            input_tokens = int(usage_amount)
        elif "OutputToken" in usage_type or "OutputChar" in usage_type:
            output_tokens = int(usage_amount)

        yield {
            "source": "cur",
            "event_time": _parse_date(row.get("line_item_usage_start_date")) + "T00:00:00+00:00",
            "date": _parse_date(row.get("line_item_usage_start_date")),
            "account_id": str(row.get("line_item_usage_account_id", "")),
            "region": str(row.get("product_region", "") or ""),
            "model_id": model_id,
            "model_family": ".".join(model_id.split(".")[:2]).split(":")[0] if model_id else "",
            "tenant_id": _resolve_tag(row, tags.get("tenant_id", [])) or "_unknown",
            "user_id": iam_principal,
            "session_id": "",
            "trace_id": "",
            "workload": _resolve_tag(row, tags.get("workload", [])),
            "team": _resolve_tag(row, tags.get("team", [])),
            "environment": _resolve_tag(row, tags.get("environment", [])),
            "request_id": str(row.get("line_item_resource_id", "") or ""),
            "cur_line_item_id": str(row.get("identity_line_item_id", "") or ""),
            "product_code": product_code,
            "usage_type": usage_type,
            "usage_amount": usage_amount,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "latency_ms": 0,
            "estimated_cost_usd": cost,
            "actual_cost_usd": cost,
            "iam_principal": iam_principal,
            "guardrail_action": "",
            "injection_detected": False,
            "pii_detected": False,
            "error_code": "",
            "inference_profile_id": "",
            "inference_profile_name": "",
        }
