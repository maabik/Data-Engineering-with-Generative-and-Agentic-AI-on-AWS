#!/usr/bin/env python3
"""Trigger oae-batch-eval once, ad-hoc.

Useful right after deploy to seed the Bedrock Evaluations console with one
job per agent without waiting for the next scheduled run.

Usage:
    python3 run_batch_now.py             # evaluate yesterday (UTC)
    python3 run_batch_now.py 2026-06-02  # evaluate a specific date
"""

from __future__ import annotations

import json
import sys

import boto3

REGION = "us-east-1"
LAMBDA_NAME = "oae-batch-eval"


def main() -> int:
    payload: dict = {}
    if len(sys.argv) > 1:
        payload["date"] = sys.argv[1]

    client = boto3.client("lambda", region_name=REGION)
    resp = client.invoke(
        FunctionName=LAMBDA_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )
    body = resp["Payload"].read().decode()
    print(body)

    parsed = json.loads(body)
    submitted = parsed.get("submitted", [])
    skipped = parsed.get("skipped", [])
    print(f"\nsubmitted: {len(submitted)}    skipped: {len(skipped)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
