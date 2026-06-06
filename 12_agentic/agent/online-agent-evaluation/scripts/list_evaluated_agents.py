#!/usr/bin/env python3
"""Show every agent the evaluator is currently subscribed to."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

import boto3

DEFAULT_REGIONS = ["us-east-1", "us-west-2"]
PREFIX = "oae"
SUB_FILTER_NAME = f"{PREFIX}-sub"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--regions", nargs="+", default=DEFAULT_REGIONS)
    args = p.parse_args()

    account = boto3.client("sts").get_caller_identity()["Account"]
    rows: list[dict] = []

    for region in args.regions:
        bucket = f"{PREFIX}-{account}-{region}"
        s3 = boto3.client("s3", region_name=region)
        try:
            obj = s3.get_object(Bucket=bucket, Key="registry.json")
            registry = json.loads(obj["Body"].read())
        except s3.exceptions.NoSuchBucket:
            print(f"  no bucket in {region}; skipping", file=sys.stderr)
            continue
        except s3.exceptions.NoSuchKey:
            continue

        rates_table = boto3.resource("dynamodb", region_name="us-east-1").Table(f"{PREFIX}-rates")
        for agent in registry.get("agents", []):
            if agent["region"] != region:
                continue
            log_group = agent["log_group"]
            logs_client = boto3.client("logs", region_name=region)
            try:
                filters = logs_client.describe_subscription_filters(logGroupName=log_group)["subscriptionFilters"]
            except logs_client.exceptions.ResourceNotFoundException:
                continue
            attached = next((f for f in filters if f["filterName"] == SUB_FILTER_NAME), None)
            if not attached:
                continue

            rate = "(default)"
            try:
                item = rates_table.get_item(Key={"agent_name": agent["name"]}).get("Item")
                if item and "sample_rate" in item:
                    rate = f"{float(item['sample_rate']):.3f}"
            except Exception:
                pass

            verdicts_24h = _verdict_count_24h(s3, bucket, agent["name"])

            rows.append({
                "name": agent["name"],
                "type": agent["type"],
                "region": region,
                "log_group": log_group,
                "rate": rate,
                "verdicts_24h": verdicts_24h,
            })

    if not rows:
        print("(no subscribed agents found)")
        return 0

    print(f"{'AGENT':<32} {'TYPE':<20} {'REGION':<10} {'RATE':<10} {'VERDICTS_24H':<12}")
    print("-" * 90)
    for r in rows:
        print(f"{r['name']:<32} {r['type']:<20} {r['region']:<10} {r['rate']:<10} {r['verdicts_24h']:<12}")
    return 0


def _verdict_count_24h(s3, bucket: str, agent_name: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    total = 0
    for dt in (today, yesterday):
        prefix = f"verdicts/agent_name={agent_name}/dt={dt}/"
        token = None
        while True:
            kwargs = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            total += resp.get("KeyCount", 0)
            token = resp.get("NextContinuationToken")
            if not token:
                break
    return total


if __name__ == "__main__":
    sys.exit(main())
