"""Pull Cost Explorer data grouped by chargeback tags and write Parquet-ish JSON to S3.

Triggered on a schedule (EventBridge rule, daily). Pulls daily unblended cost
for the previous 30 days, grouped by tag:team, tag:business_unit, tag:pipeline_id
and SERVICE. The output is written to S3 in a single line-delimited JSON file
per run, partitioned by ingest date so Athena can query the running history.

Cost Explorer GetCostAndUsage caps at 2 GroupBy dimensions, so we make 2 calls:
  1. team + service
  2. pipeline_id + service
and union them into one stream with `dimension` and `dimension_value`.

Env vars:
    OUTPUT_BUCKET, OUTPUT_PREFIX
    DAYS_BACK   (default 30)
    GRANULARITY (default DAILY)
    METRIC      (default UnblendedCost)
"""
from __future__ import annotations

import io
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import boto3
from botocore.config import Config

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_CFG = Config(retries={"mode": "standard", "max_attempts": 5})
CE = boto3.client("ce", config=_CFG, region_name="us-east-1")
S3 = boto3.client("s3", config=_CFG)

OUTPUT_BUCKET = os.environ["OUTPUT_BUCKET"]
OUTPUT_PREFIX = os.environ.get("OUTPUT_PREFIX", "cost/").rstrip("/") + "/"
DAYS_BACK = int(os.environ.get("DAYS_BACK", "30"))
GRANULARITY = os.environ.get("GRANULARITY", "DAILY")
METRIC = os.environ.get("METRIC", "UnblendedCost")


def _query(start: str, end: str, group_by: list[dict]) -> list[dict]:
    rows: list[dict] = []
    token: str | None = None
    while True:
        kwargs = dict(
            TimePeriod={"Start": start, "End": end},
            Granularity=GRANULARITY,
            Metrics=[METRIC],
            GroupBy=group_by,
        )
        if token:
            kwargs["NextPageToken"] = token
        resp = CE.get_cost_and_usage(**kwargs)
        for window in resp.get("ResultsByTime", []):
            window_start = window["TimePeriod"]["Start"]
            for g in window.get("Groups", []):
                rows.append(
                    {
                        "usage_date": window_start,
                        "keys": g["Keys"],
                        "amount": Decimal(g["Metrics"][METRIC]["Amount"]),
                        "unit": g["Metrics"][METRIC]["Unit"],
                    }
                )
        token = resp.get("NextPageToken")
        if not token:
            break
    return rows


def _flatten(group_a: str, dimension_label: str, raw: list[dict]) -> list[dict]:
    out = []
    for r in raw:
        keys = r["keys"]
        # CE returns tag groups as "tagkey$tagvalue"; service is plain.
        dim_value = keys[0].split("$", 1)[1] if "$" in keys[0] else keys[0]
        service = keys[1] if len(keys) > 1 else None
        out.append(
            {
                "usage_date": r["usage_date"],
                "dimension": dimension_label,
                "dimension_value": dim_value or "untagged",
                "service": service,
                "amount": float(r["amount"]),
                "unit": r["unit"],
                "group_by_tag": group_a,
            }
        )
    return out


def lambda_handler(event, context):
    end = date.today()
    start = end - timedelta(days=DAYS_BACK)
    s, e = start.isoformat(), end.isoformat()
    logger.info("fetching CE %s..%s granularity=%s", s, e, GRANULARITY)

    rows: list[dict] = []
    for tag_key, label in [
        ("team", "team"),
        ("business_unit", "business_unit"),
        ("pipeline_id", "pipeline_id"),
    ]:
        try:
            raw = _query(
                s,
                e,
                [
                    {"Type": "TAG", "Key": tag_key},
                    {"Type": "DIMENSION", "Key": "SERVICE"},
                ],
            )
        except CE.exceptions.DataUnavailableException as exc:
            logger.warning("tag %s unavailable: %s", tag_key, exc)
            continue
        rows.extend(_flatten(tag_key, label, raw))
        logger.info("tag=%s rows=%d", tag_key, len(raw))

    now = datetime.now(timezone.utc)
    out_key = (
        f"{OUTPUT_PREFIX}ingest_date={now:%Y-%m-%d}/"
        f"costs_{now:%Y%m%dT%H%M%SZ}.jsonl"
    )
    buf = io.BytesIO()
    for row in rows:
        buf.write((json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8"))
    body = buf.getvalue()
    S3.put_object(
        Bucket=OUTPUT_BUCKET,
        Key=out_key,
        Body=body,
        ContentType="application/x-ndjson",
        ServerSideEncryption="AES256",
    )
    logger.info("wrote s3://%s/%s rows=%d bytes=%d", OUTPUT_BUCKET, out_key, len(rows), len(body))
    return {
        "Success": True,
        "Rows": len(rows),
        "Output": f"s3://{OUTPUT_BUCKET}/{out_key}",
        "Window": {"start": s, "end": e},
    }
