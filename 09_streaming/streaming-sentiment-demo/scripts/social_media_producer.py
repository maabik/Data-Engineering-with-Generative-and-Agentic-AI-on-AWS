"""
Stream synthetic social media posts into Kinesis for the sentiment demo.

Posts are generated on demand by Amazon Bedrock so the demo doesn't loop the
same 10 strings — see bedrock_post_generator.py. Pass --tone to bias toward
positive/negative/neutral/crisis content; default is a realistic mix.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

import boto3

from bedrock_post_generator import BedrockPostGenerator

STREAM_NAME = os.environ.get("STREAM_NAME", "social-media-posts-stream")
REGION = os.environ.get("AWS_REGION", "us-east-1")
INTERVAL_SECONDS = float(os.environ.get("INTERVAL_SECONDS", "1.0"))

kinesis_client = boto3.client("kinesis", region_name=REGION)


def send_post(post: dict) -> None:
    post["timestamp"] = datetime.now(timezone.utc).isoformat()
    data = json.dumps(post)
    response = kinesis_client.put_record(
        StreamName=STREAM_NAME,
        Data=data,
        PartitionKey=post["userId"],
    )
    print(f"sent: {data}")
    print(f"  shard={response['ShardId']} seq={response['SequenceNumber']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tone",
        choices=("positive", "negative", "neutral", "crisis", "mixed"),
        default="mixed",
        help="Bias the generated posts toward a tone (default: mixed).",
    )
    parser.add_argument(
        "--brand",
        default=None,
        help="If set, every generated post mentions this brand by name.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=0,
        help="Send N posts and exit. Default 0 means run until Ctrl-C.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=INTERVAL_SECONDS,
        help=f"Seconds between posts (default {INTERVAL_SECONDS}).",
    )
    args = parser.parse_args()

    print(
        f"streaming → {STREAM_NAME} every {args.interval}s "
        f"(tone={args.tone}, brand={args.brand or '-'})"
    )

    generator = BedrockPostGenerator()
    sent = 0
    try:
        while True:
            send_post(generator.next_post(tone=args.tone, brand=args.brand))
            sent += 1
            if args.count and sent >= args.count:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\nstopped after {sent} posts.")


if __name__ == "__main__":
    main()
