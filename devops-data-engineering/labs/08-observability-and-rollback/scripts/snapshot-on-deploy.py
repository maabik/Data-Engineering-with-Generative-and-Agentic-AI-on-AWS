#!/usr/bin/env python3
"""After a successful deploy, tag the current Iceberg snapshot with the
deploy ID so incident triage can replay the table state at that point.

Run as a CI step in release.yml after smoke + DQ pass.
"""
from __future__ import annotations

import argparse
import sys
import time

import boto3


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--database", required=True)
    p.add_argument("--table", required=True)
    p.add_argument("--deploy-id", required=True)
    p.add_argument("--output-location", required=True)
    args = p.parse_args()

    athena = boto3.client("athena")
    sql = f"ALTER TABLE {args.database}.{args.table} CREATE BRANCH `deploy-{args.deploy_id}`"

    qid = athena.start_query_execution(
        QueryString=sql,
        ResultConfiguration={"OutputLocation": args.output_location},
    )["QueryExecutionId"]

    deadline = time.time() + 60
    while time.time() < deadline:
        s = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if s in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        time.sleep(2)

    if s != "SUCCEEDED":
        print(f"Snapshot tag failed: {s}", file=sys.stderr)
        return 1
    print(f"Tagged snapshot as deploy-{args.deploy_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
