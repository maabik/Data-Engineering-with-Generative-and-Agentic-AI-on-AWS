"""
Simulate a brand PR event by streaming Bedrock-generated posts into Kinesis.

Replaces the static 15-post pool. Posts are generated per scenario phase so
each run is fresh and the sentiment-analysis Lambda sees a realistic narrative
arc (baseline → positive bump → baseline → crisis → recovery).

Examples:
    python pr_event_simulator.py --scenario negative --brand "ACME Corp"
    python pr_event_simulator.py --scenario full --duration 90 --intensity 0.8
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from datetime import datetime, timezone

import boto3

from bedrock_post_generator import BedrockPostGenerator

STREAM_NAME = os.environ.get("STREAM_NAME", "social-media-posts-stream")
REGION = os.environ.get("AWS_REGION", "us-east-1")
INTERVAL_SECONDS = float(os.environ.get("INTERVAL_SECONDS", "0.5"))

kinesis_client = boto3.client("kinesis", region_name=REGION)


def send_post(post: dict) -> bool:
    try:
        post["timestamp"] = datetime.now(timezone.utc).isoformat()
        data = json.dumps(post)
        response = kinesis_client.put_record(
            StreamName=STREAM_NAME, Data=data, PartitionKey=post["userId"]
        )
        print(f"sent: {data}")
        print(f"  shard={response['ShardId']} seq={response['SequenceNumber']}")
        return True
    except Exception as exc:
        print(f"error sending post: {exc}")
        return False


def simulate_baseline(generator: BedrockPostGenerator, brand: str | None,
                      duration: int) -> None:
    print(f"\n--- baseline activity ({duration}s) ---")
    end = time.time() + duration
    while time.time() < end:
        send_post(generator.next_post(tone="neutral", brand=brand))
        time.sleep(INTERVAL_SECONDS)


def simulate_event(generator: BedrockPostGenerator, brand: str | None,
                   tone: str, intensity: float, duration: int) -> None:
    print(f"\n--- {tone} PR event (intensity {intensity}, {duration}s) ---")
    end = time.time() + duration
    while time.time() < end:
        chosen_tone = tone if random.random() < intensity else "neutral"
        send_post(generator.next_post(tone=chosen_tone, brand=brand))
        time.sleep(INTERVAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        choices=("baseline", "positive", "negative", "crisis", "full"),
        default="full",
        help="Which scenario to run (default: full arc).",
    )
    parser.add_argument(
        "--brand",
        default="ACME Corp",
        help="Brand name to mention in event posts (default: ACME Corp).",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=60,
        help="Seconds per phase (default 60).",
    )
    parser.add_argument(
        "--intensity",
        type=float,
        default=0.8,
        help="Fraction 0.0-1.0 of posts that match the event tone (default 0.8).",
    )
    args = parser.parse_args()

    generator = BedrockPostGenerator()

    if args.scenario == "baseline":
        simulate_baseline(generator, args.brand, args.duration)
    elif args.scenario in ("positive", "negative", "crisis"):
        simulate_event(
            generator, args.brand, args.scenario, args.intensity, args.duration
        )
    else:  # full arc
        print("running full PR-event simulation arc")
        simulate_baseline(generator, args.brand, args.duration)
        simulate_event(generator, args.brand, "positive", args.intensity, args.duration)
        simulate_baseline(generator, args.brand, args.duration)
        simulate_event(generator, args.brand, "crisis", args.intensity, args.duration)
        simulate_baseline(generator, args.brand, args.duration)
        print("\nsimulation complete.")


if __name__ == "__main__":
    main()
