#!/usr/bin/env python3
"""Seed a sandbox's raw S3 zone from a redacted prod snapshot.

For the chapter codebase we generate synthetic data instead of pulling
from a real prod snapshot (which would require existing infra). The
shape of the function mirrors what a real seeder does:

  1. Read from a privileged "snapshots" bucket (in real life, gated by
     Lake Formation tag-based access).
  2. Apply redactor (here, just trims to ~1% of rows; in real life,
     PII tokenisation via Comprehend or an internal tool).
  3. Write to the sandbox's raw bucket so the pipeline has data to
     process.

Usage:
  python sandbox-seed.py --raw-bucket alice-ref-raw-123456789012
"""
from __future__ import annotations

import argparse
import io
import random
import sys

import boto3


def synth_orders(n: int) -> bytes:
    rng = random.Random(0)
    lines = ["order_id,customer_id,total,status"]
    for i in range(n):
        cust = rng.choice(["c1", "c2", "c3", "c4", "c5"])
        total = round(rng.uniform(5.0, 500.0), 2)
        status = rng.choice(["paid", "paid", "paid", "cancelled"])
        lines.append(f"{i},{cust},{total},{status}")
    return ("\n".join(lines) + "\n").encode()


def synth_customers() -> bytes:
    return (
        "customer_id,region\n"
        "c1,EU\nc2,NA\nc3,APAC\nc4,EU\nc5,NA\n"
    ).encode()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--raw-bucket", required=True)
    p.add_argument("--orders-rows", type=int, default=1000)
    args = p.parse_args()

    s3 = boto3.client("s3")
    s3.put_object(Bucket=args.raw_bucket, Key="orders/orders.csv", Body=synth_orders(args.orders_rows))
    s3.put_object(Bucket=args.raw_bucket, Key="customers/customers.csv", Body=synth_customers())
    print(f"Seeded s3://{args.raw_bucket}/{{orders,customers}}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
