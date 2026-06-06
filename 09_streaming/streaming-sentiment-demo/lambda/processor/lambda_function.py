"""Kinesis processor Lambda.

For each Kinesis record:
  1. Decode the JSON post.
  2. Synchronously invoke the sentiment-analysis Lambda for a 1-5 score.
  3. Write the enriched record to S3 with Hive-style year/month/day/hour
     partitioning under sentiment-data/.
  4. Publish CloudWatch metrics (avg sentiment, distribution, per-platform).
"""
import base64
import json
import logging
import os
from datetime import datetime, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SENTIMENT_FUNCTION_NAME = os.environ["SENTIMENT_FUNCTION_NAME"]
S3_BUCKET_NAME = os.environ["S3_BUCKET_NAME"]
METRIC_NAMESPACE = "SocialMediaSentiment"

lambda_client = boto3.client("lambda")
s3 = boto3.client("s3")
cloudwatch = boto3.client("cloudwatch")


def _invoke_sentiment(post):
    response = lambda_client.invoke(
        FunctionName=SENTIMENT_FUNCTION_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps({"content": post.get("content", "")}),
    )
    payload = json.loads(response["Payload"].read())
    score = int(payload.get("sentimentScore", 3))
    return max(1, min(5, score))


def _store(post):
    now = datetime.now(timezone.utc)
    key = (
        "sentiment-data/"
        f"year={now.year:04d}/"
        f"month={now.month:02d}/"
        f"day={now.day:02d}/"
        f"hour={now.hour:02d}/"
        f"{int(now.timestamp() * 1000)}-{post['id']}.json"
    )
    s3.put_object(
        Bucket=S3_BUCKET_NAME,
        Key=key,
        Body=json.dumps(post).encode("utf-8"),
        ContentType="application/json",
    )


def _emit_metrics(scores, platforms):
    if not scores:
        return
    metric_data = [
        {
            "MetricName": "AverageSentiment",
            "Value": sum(scores) / len(scores),
            "Unit": "None",
        },
        {
            "MetricName": "PostCount",
            "Value": len(scores),
            "Unit": "Count",
        },
    ]
    for n in (1, 2, 3, 4, 5):
        count = sum(1 for s in scores if s == n)
        if count:
            metric_data.append(
                {"MetricName": f"SentimentScore{n}", "Value": count, "Unit": "Count"}
            )
    for platform, count in platforms.items():
        metric_data.append(
            {"MetricName": f"Platform_{platform}", "Value": count, "Unit": "Count"}
        )
    cloudwatch.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=metric_data)


def lambda_handler(event, _context):
    scores = []
    platforms = {}
    processed = 0

    for record in event.get("Records", []):
        try:
            payload = base64.b64decode(record["kinesis"]["data"])
            post = json.loads(payload)
        except (ValueError, KeyError):
            logger.exception("malformed Kinesis record")
            continue

        post.setdefault("id", record["kinesis"]["sequenceNumber"])

        try:
            score = _invoke_sentiment(post)
        except Exception:  # noqa: BLE001 — record-level failure
            logger.exception("sentiment invocation failed")
            continue

        post["sentimentScore"] = score
        post["processedAt"] = datetime.now(timezone.utc).isoformat()

        try:
            _store(post)
        except Exception:  # noqa: BLE001
            logger.exception("S3 write failed")
            continue

        scores.append(score)
        platform = post.get("platform", "unknown")
        platforms[platform] = platforms.get(platform, 0) + 1
        processed += 1

    try:
        _emit_metrics(scores, platforms)
    except Exception:  # noqa: BLE001
        logger.exception("CloudWatch put_metric_data failed (non-fatal)")

    return {"processed": processed, "received": len(event.get("Records", []))}
