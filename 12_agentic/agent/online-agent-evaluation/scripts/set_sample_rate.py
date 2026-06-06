#!/usr/bin/env python3
"""Set or clear a per-agent sample rate override."""

from __future__ import annotations

import argparse
import sys

import boto3

RATES_TABLE = "oae-rates"
RATES_REGION = "us-east-1"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("agent_name")
    p.add_argument("rate", help="Sample rate 0.0–1.0, or 'clear' to remove the override")
    args = p.parse_args()

    table = boto3.resource("dynamodb", region_name=RATES_REGION).Table(RATES_TABLE)

    if args.rate == "clear":
        table.delete_item(Key={"agent_name": args.agent_name})
        print(f"cleared override for {args.agent_name}")
        return 0

    rate = float(args.rate)
    if not 0.0 <= rate <= 1.0:
        print("rate must be 0.0–1.0", file=sys.stderr)
        return 1

    table.put_item(Item={"agent_name": args.agent_name, "sample_rate": str(rate)})
    print(f"set {args.agent_name} sample_rate={rate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
