"""
Bedrock-backed social media post generator.

Both producers (social_media_producer.py, pr_event_simulator.py) used to ship
small hand-written pools of 10–15 posts that repeated in a loop. That made the
sentiment-analysis pipeline look impressive in a demo for about 30 seconds and
then started replaying the same posts. This module replaces those pools with
on-demand calls to Amazon Bedrock so each batch is fresh, varied, and tunable.

Usage:
    gen = BedrockPostGenerator()
    posts = gen.generate(tone="negative", brand="ACME Corp", count=8)

The generator caches a small in-memory queue per (tone, brand) so the producer
loop only pays the model latency once per refill, not once per post. If Bedrock
fails (no credentials, model access denied, throttling), generate() falls back
to a tiny static pool so the demo still runs.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import uuid
from typing import Iterable

import boto3

LOG = logging.getLogger(__name__)

DEFAULT_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)
DEFAULT_BATCH_SIZE = int(os.environ.get("POST_BATCH_SIZE", "8"))

PLATFORMS = ("Twitter", "Facebook", "Instagram", "LinkedIn", "Reddit")

TONE_PROMPTS = {
    "positive": (
        "praise, excitement, gratitude, success stories, or product love"
    ),
    "negative": (
        "frustration, complaints, anger, disappointment, or service failures"
    ),
    "neutral": (
        "factual updates, questions, observations, or news without strong emotion"
    ),
    "crisis": (
        "alarm, outrage, calls for accountability, viral negative reaction, "
        "and demands for response — escalating tone consistent with a brand crisis"
    ),
    "mixed": (
        "a realistic mix of positive, negative, and neutral takes — different "
        "users reacting differently to everyday topics"
    ),
}

# Tiny safety net so the demo still runs if Bedrock is unreachable.
FALLBACK_POSTS: dict[str, list[str]] = {
    "positive": [
        "Just had the best customer service experience! Highly recommend.",
        "This product totally exceeded my expectations — worth every penny.",
        "Big thanks to the team for the quick fix today.",
    ],
    "negative": [
        "Worst experience I've had in years. Avoid at all costs.",
        "Three hours on hold and still no resolution. Terrible.",
        "This update broke half the features I actually used.",
    ],
    "neutral": [
        "Heard there's a new release coming next week — anyone tried the beta?",
        "Anyone know if the store on Main St is still open on Sundays?",
        "Reading the quarterly report now, will share thoughts later.",
    ],
    "crisis": [
        "BREAKING: documents leaked showing they knew about the issue for months.",
        "Boycotting after what came out today. This is unacceptable.",
        "How is this company still in business after that announcement?",
    ],
    "mixed": [
        "Honestly the product is great, but the support team needs work.",
        "Mixed feelings on the rebrand — logo is sharp, app is slower.",
        "Some good, some bad. Wouldn't write them off yet.",
    ],
}


class BedrockPostGenerator:
    """Thread-safe generator that pulls cached posts from Bedrock on demand."""

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        batch_size: int = DEFAULT_BATCH_SIZE,
        region: str | None = None,
    ) -> None:
        self.model_id = model_id
        self.batch_size = max(1, batch_size)
        self.region = region or os.environ.get("AWS_REGION", "us-east-1")
        self._client = boto3.client("bedrock-runtime", region_name=self.region)
        self._cache: dict[tuple[str, str | None], list[dict]] = {}
        self._lock = threading.Lock()

    # ---- public API -------------------------------------------------------

    def next_post(self, tone: str = "mixed", brand: str | None = None) -> dict:
        """Return a single post, refilling the per-(tone,brand) queue if empty."""
        key = (tone, brand)
        with self._lock:
            queue = self._cache.get(key)
            if not queue:
                queue = self._refill(tone, brand)
                self._cache[key] = queue
            return queue.pop()

    def generate(
        self, tone: str = "mixed", brand: str | None = None, count: int = 1
    ) -> list[dict]:
        return [self.next_post(tone, brand) for _ in range(count)]

    # ---- internals --------------------------------------------------------

    def _refill(self, tone: str, brand: str | None) -> list[dict]:
        try:
            posts = self._invoke_bedrock(tone, brand, self.batch_size)
            if posts:
                return posts
        except Exception as exc:  # noqa: BLE001 — demo must survive any AWS-side failure
            LOG.warning(
                "Bedrock post generation failed (%s) — using fallback pool", exc
            )
        return self._fallback_batch(tone, brand)

    def _invoke_bedrock(
        self, tone: str, brand: str | None, count: int
    ) -> list[dict]:
        instructions = TONE_PROMPTS.get(tone, TONE_PROMPTS["mixed"])
        brand_hint = (
            f" Posts must mention the brand '{brand}' by name."
            if brand
            else " Posts can reference any everyday topic — coffee, weather, travel, tech, work."
        )
        prompt = (
            f"Generate {count} realistic short social media posts conveying "
            f"{instructions}.{brand_hint} Vary platforms, vocabulary, and "
            "user voice. Keep each post under 280 characters. Respond with "
            "ONLY a JSON array of objects, each with keys: content (string), "
            "platform (one of Twitter, Facebook, Instagram, LinkedIn, Reddit), "
            "tags (array of 1-3 lowercase keyword strings). No prose, no "
            "code fences, no preamble."
        )

        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 1500,
                "temperature": 0.9,
                "messages": [{"role": "user", "content": prompt}],
            }
        )

        response = self._client.invoke_model(modelId=self.model_id, body=body)
        payload = json.loads(response["body"].read())
        text = "".join(
            chunk.get("text", "")
            for chunk in payload.get("content", [])
            if chunk.get("type") == "text"
        ).strip()
        if not text:
            raise ValueError("empty response from Bedrock")

        items = _coerce_json_array(text)
        return [self._wrap(item) for item in items if isinstance(item, dict)]

    def _fallback_batch(self, tone: str, brand: str | None) -> list[dict]:
        pool = FALLBACK_POSTS.get(tone, FALLBACK_POSTS["mixed"])
        out = []
        for content in pool:
            text = (
                content.replace("they ", f"{brand} ").replace("This company", brand)
                if brand
                else content
            )
            out.append(
                self._wrap(
                    {
                        "content": text,
                        "platform": random.choice(PLATFORMS),
                        "tags": [tone],
                    }
                )
            )
        random.shuffle(out)
        return out

    @staticmethod
    def _wrap(item: dict) -> dict:
        return {
            "userId": f"user-{uuid.uuid4().hex[:8]}",
            "content": str(item.get("content", "")).strip()[:500],
            "platform": item.get("platform") if item.get("platform") in PLATFORMS
            else random.choice(PLATFORMS),
            "tags": _normalize_tags(item.get("tags", [])),
        }


def _normalize_tags(raw: Iterable) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for tag in raw:
        if not isinstance(tag, str):
            continue
        cleaned = tag.strip().lstrip("#").lower()
        if cleaned:
            out.append(cleaned)
        if len(out) >= 5:
            break
    return out


def _coerce_json_array(text: str) -> list:
    """Bedrock occasionally wraps JSON in code fences — pull the array out."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON array in response: {text[:120]!r}")
    return json.loads(text[start : end + 1])
