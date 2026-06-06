"""Span loaders: reads raw RunSpan events from S3 (Firehose prefix) or Athena."""

from __future__ import annotations

import gzip
import json
import logging
from datetime import date, timedelta
from typing import Generator

import boto3

logger = logging.getLogger(__name__)


def _iter_s3_prefix(bucket: str, prefix: str, s3_client) -> Generator[dict, None, None]:
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            try:
                body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
                if key.endswith(".gz"):
                    body = gzip.decompress(body)
                for line in body.decode("utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue
            except Exception as e:
                logger.error("Failed reading %s: %s", key, e)


def load_spans_from_s3(
    bucket: str,
    prefix: str = "",
    target_date: date | None = None,
    s3_client=None,
) -> Generator[dict, None, None]:
    """Yield raw span dicts from Firehose-landed S3 files for a given date."""
    if s3_client is None:
        s3_client = boto3.client("s3")

    # Narrow the prefix to the target date if provided (Firehose partitions by year/month/day)
    if target_date:
        date_prefix = f"{prefix.rstrip('/')}/{target_date.year}/{target_date.month:02d}/{target_date.day:02d}/"
    else:
        date_prefix = prefix

    yield from _iter_s3_prefix(bucket, date_prefix, s3_client)


def load_spans_from_athena(
    database: str,
    spans_table: str,
    target_date: date,
    athena_output: str,
    workgroup: str = "primary",
    athena_client=None,
) -> Generator[dict, None, None]:
    """Yield span dicts by querying the Iceberg spans table directly via Athena."""
    import time

    if athena_client is None:
        athena_client = boto3.client("athena")

    sql = f"""
SELECT *
FROM {database}.{spans_table}
WHERE date = DATE '{target_date.isoformat()}'
""".strip()

    resp = athena_client.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": database},
        ResultConfiguration={"OutputLocation": athena_output},
        WorkGroup=workgroup,
    )
    qid = resp["QueryExecutionId"]

    for _ in range(120):
        status = athena_client.get_query_execution(QueryExecutionId=qid)
        state = status["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            reason = status["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"Athena query {state}: {reason}")
        time.sleep(5)
    else:
        raise TimeoutError(f"Athena query {qid} timed out")

    paginator = athena_client.get_paginator("get_query_results")
    columns: list[str] = []
    for page in paginator.paginate(QueryExecutionId=qid):
        rows = page["ResultSet"]["Rows"]
        if not columns:
            columns = [c["VarCharValue"] for c in rows[0]["Data"]]
            rows = rows[1:]
        for row in rows:
            values = [c.get("VarCharValue", "") for c in row["Data"]]
            yield dict(zip(columns, values))


def nightly_stitch(
    span_bucket: str,
    span_prefix: str,
    target_date: date,
    runs_bucket: str,
    runs_prefix: str = "agent-runs/",
    s3_client=None,
) -> int:
    """Load spans for target_date, stitch into runs, write NDJSON to runs_bucket."""
    from .stitch import stitch_runs

    if s3_client is None:
        s3_client = boto3.client("s3")

    spans = list(load_spans_from_s3(span_bucket, span_prefix, target_date, s3_client))
    logger.info("Loaded %d spans for %s", len(spans), target_date)

    if not spans:
        logger.info("No spans found — skipping stitch")
        return 0

    runs = stitch_runs(spans)
    logger.info("Stitched %d runs", len(runs))

    # Write runs as NDJSON partitioned by date
    key = f"{runs_prefix.rstrip('/')}/date={target_date.isoformat()}/runs.ndjson"
    body = "\n".join(json.dumps(r.to_dict(), default=str) for r in runs) + "\n"
    s3_client.put_object(
        Bucket=runs_bucket,
        Key=key,
        Body=body.encode("utf-8"),
        ContentType="application/x-ndjson",
    )
    logger.info("Wrote %d run records to s3://%s/%s", len(runs), runs_bucket, key)
    return len(runs)
