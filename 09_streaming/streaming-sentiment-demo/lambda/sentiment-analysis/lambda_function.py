"""Sentiment analysis Lambda.

Invoked by the processor Lambda with a single social media post. Calls Bedrock
Claude 3.5 Haiku and returns an integer 1-5 sentiment score.
"""
import json
import logging
import os
import re

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Prefer the inference profile; fall back to the foundation model id.
MODEL_ID = os.environ.get(
    "BEDROCK_INFERENCE_PROFILE_ID",
    os.environ.get("MODEL_ID", "anthropic.claude-haiku-4-5-20251001-v1:0"),
)
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", os.environ.get("AWS_REGION", "us-east-1"))

bedrock = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)

PROMPT_TEMPLATE = (
    "Rate the sentiment of this social media post on a scale of 1 to 5 where:\n"
    "1 = very negative, 2 = negative, 3 = neutral, 4 = positive, 5 = very positive.\n"
    "Reply with ONLY the single integer and nothing else.\n\n"
    "Post: {content}"
)


def _score_from_text(text: str) -> int:
    match = re.search(r"[1-5]", text)
    return int(match.group(0)) if match else 3


def _invoke_bedrock(content: str) -> int:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 5,
        "temperature": 0,
        "messages": [
            {"role": "user", "content": PROMPT_TEMPLATE.format(content=content)},
        ],
    }
    response = bedrock.invoke_model(modelId=MODEL_ID, body=json.dumps(body))
    payload = json.loads(response["body"].read())
    text = payload["content"][0]["text"].strip()
    return _score_from_text(text)


def lambda_handler(event, _context):
    content = event.get("content")
    if not content:
        return {"sentimentScore": 3, "error": "no content"}

    try:
        score = _invoke_bedrock(content)
    except Exception as exc:  # noqa: BLE001 — log and fail open with neutral
        logger.exception("Bedrock call failed: %s", exc)
        return {"sentimentScore": 3, "error": str(exc)}

    return {"sentimentScore": score}
