#!/usr/bin/env python3
"""Seed the SQL Evaluations Agent steering doc to its starting state.

This is idempotent: it writes the seed text only if the object does not
already exist, so it won't clobber lessons the agent has already recorded.
"""

import os
import sys

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
BUCKET = os.environ["STEERING_BUCKET"]
KEY = os.environ.get("STEERING_KEY", "steering-doc.md")
FORCE = "--force" in sys.argv

SEED = """# SQL Evaluations Agent — Steering Doc

This file is the agent's running memory. The agent reads it at the start of
every evaluation and appends one short lesson at the end if something
non-obvious surfaced. Each entry is timestamped UTC.

## Heuristics the agent should always remember
- A query without a WHERE clause that uses DELETE, UPDATE, or TRUNCATE is a
  high-severity security finding even when the caller claims it is intentional.
- `SELECT *` against a partitioned/wide table is an optimization finding
  unless the caller explicitly asked for full row materialization.
- String concatenation of caller-provided values into SQL is always an
  injection finding; recommend parameter binding.
- DDL inside what was supposed to be a read-only analytics flow is a
  separation-of-concerns violation; flag and reject.

## Lessons learned
"""


def main() -> int:
    s3 = boto3.client("s3", region_name=REGION)
    if not FORCE:
        try:
            s3.head_object(Bucket=BUCKET, Key=KEY)
            print(f"steering doc s3://{BUCKET}/{KEY} already exists; skipping (use --force to overwrite)")
            return 0
        except s3.exceptions.ClientError as e:
            if e.response["Error"]["Code"] not in {"404", "NoSuchKey"}:
                raise
    s3.put_object(Bucket=BUCKET, Key=KEY, Body=SEED.encode("utf-8"), ContentType="text/markdown")
    print(f"wrote seed steering doc to s3://{BUCKET}/{KEY}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
