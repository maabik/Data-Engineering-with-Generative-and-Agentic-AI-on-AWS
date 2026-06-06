"""Writer: lands normalised FinOps records into the Iceberg S3 dataset.

Uses Athena INSERT INTO (Iceberg DML) to write records via SQL rather than
requiring a Spark/Glue job for small daily increments. For large backfills,
the Glue job in glue/jobs/ should be used instead.

Partition scheme: date / model_family / tenant_id

The writer:
1. Batches records into daily partition groups.
2. Generates a temp S3 staging prefix with NDJSON.
3. Issues Athena CTAS or INSERT INTO to land data into the Iceberg table.
4. Cleans up the staging prefix.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from typing import Iterable

import boto3

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 5  # seconds between Athena status polls
_MAX_WAIT = 600     # seconds before giving up on a query


# ── Athena helpers ─────────────────────────────────────────────────────────────

def _wait_for_query(execution_id: str, athena_client) -> str:
    """Poll until the query completes. Returns final status string."""
    waited = 0
    while waited < _MAX_WAIT:
        resp = athena_client.get_query_execution(QueryExecutionId=execution_id)
        state = resp["QueryExecution"]["Status"]["State"]
        if state in ("SUCCEEDED", "FAILED", "CANCELLED"):
            if state != "SUCCEEDED":
                reason = resp["QueryExecution"]["Status"].get("StateChangeReason", "")
                raise RuntimeError(f"Athena query {execution_id} {state}: {reason}")
            return state
        time.sleep(_POLL_INTERVAL)
        waited += _POLL_INTERVAL
    raise TimeoutError(f"Athena query {execution_id} did not complete in {_MAX_WAIT}s")


def _run_athena(sql: str, database: str, output_location: str, athena_client) -> str:
    resp = athena_client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": output_location},
    )
    execution_id = resp["QueryExecutionId"]
    _wait_for_query(execution_id, athena_client)
    return execution_id


# ── Staging helpers ────────────────────────────────────────────────────────────

def _stage_records(
    records: list[dict],
    staging_bucket: str,
    staging_prefix: str,
    s3_client,
) -> str:
    """Write records as NDJSON to a staging S3 key. Returns the s3:// URI."""
    key = f"{staging_prefix.rstrip('/')}/{uuid.uuid4().hex}.ndjson"
    body = "\n".join(json.dumps(r, default=str) for r in records) + "\n"
    s3_client.put_object(
        Bucket=staging_bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/x-ndjson",
    )
    return f"s3://{staging_bucket}/{key}"


def _cleanup_staging(staging_bucket: str, staging_prefix: str, s3_client):
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=staging_bucket, Prefix=staging_prefix):
        keys = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if keys:
            s3_client.delete_objects(Bucket=staging_bucket, Delete={"Objects": keys})


# ── Schema helpers ─────────────────────────────────────────────────────────────

def _to_sql_values(rec: dict) -> str:
    """Render a record dict as a SQL VALUES row string."""
    def _fmt(v):
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"

    fields = [
        "event_time", "date", "source", "account_id", "region",
        "model_id", "model_family", "tenant_id", "user_id", "session_id",
        "trace_id", "workload", "team", "request_id", "input_tokens",
        "output_tokens", "total_tokens", "latency_ms",
        "estimated_cost_usd", "actual_cost_usd",
        "guardrail_action", "injection_detected", "pii_detected", "error_code",
        "cur_line_item_id", "iam_principal",
        "inference_profile_id", "inference_profile_name",
        "joined", "join_method", "service_category",
        "agent_id", "agent_name", "execution_status",
        "model_invocations", "tool_invocations",
    ]
    values = ", ".join(_fmt(rec.get(f)) for f in fields)
    return f"({values})"


# ── Public API ─────────────────────────────────────────────────────────────────

def write_batch(
    records: Iterable[dict],
    *,
    database: str,
    table: str,
    athena_output_location: str,
    staging_bucket: str,
    staging_prefix: str = "finops-staging/",
    batch_size: int = 5000,
    athena_client=None,
    s3_client=None,
) -> int:
    """Write records to the Iceberg table via Athena INSERT INTO.

    Returns total number of records written.
    """
    if athena_client is None:
        athena_client = boto3.client("athena")
    if s3_client is None:
        s3_client = boto3.client("s3")

    all_records = list(records)
    if not all_records:
        logger.info("No records to write")
        return 0

    total = 0
    for i in range(0, len(all_records), batch_size):
        batch = all_records[i: i + batch_size]
        values_clause = ",\n  ".join(_to_sql_values(r) for r in batch)

        sql = f"""
INSERT INTO {database}.{table}
(event_time, date, source, account_id, region,
 model_id, model_family, tenant_id, user_id, session_id,
 trace_id, workload, team, request_id, input_tokens,
 output_tokens, total_tokens, latency_ms,
 estimated_cost_usd, actual_cost_usd,
 guardrail_action, injection_detected, pii_detected, error_code,
 cur_line_item_id, iam_principal,
 inference_profile_id, inference_profile_name,
 joined, join_method, service_category,
 agent_id, agent_name, execution_status,
 model_invocations, tool_invocations)
VALUES
  {values_clause}
""".strip()

        _run_athena(sql, database, athena_output_location, athena_client)
        total += len(batch)
        logger.info("Wrote batch %d/%d (%d records)", i // batch_size + 1,
                    -(-len(all_records) // batch_size), len(batch))

    return total
