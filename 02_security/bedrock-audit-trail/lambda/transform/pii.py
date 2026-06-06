"""PII detection and reversible tokenization via Comprehend + KMS-protected S3 store."""

from __future__ import annotations

import json
import os
import random
import re
import string
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import boto3

_comprehend = None
_s3 = None

def _token_bucket() -> str:
    return os.environ.get("TOKEN_BUCKET", "")

def _token_key_arn() -> str:
    return os.environ.get("TOKEN_KEY_ARN", "")


def _get_comprehend():
    global _comprehend
    if _comprehend is None:
        _comprehend = boto3.client("comprehend")
    return _comprehend


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


@dataclass
class PiiSpan:
    start: int
    end: int
    entity_type: str
    score: float
    token_id: str = field(default_factory=lambda: f"tok_{uuid.uuid4().hex[:12]}")


@dataclass
class PiiResult:
    detected: bool
    entity_types: list[str]
    token_ids: list[str]
    redacted_text: str


def detect_and_redact(text: str, request_id: str) -> PiiResult:
    """Detect PII with Comprehend, redact spans, store reversible tokens."""
    if not text or not text.strip():
        return PiiResult(detected=False, entity_types=[], token_ids=[], redacted_text=text)

    truncated = text[:100_000]  # Comprehend limit

    client = _get_comprehend()
    resp = client.detect_pii_entities(Text=truncated, LanguageCode="en")
    entities = resp.get("Entities", [])

    if not entities:
        return PiiResult(detected=False, entity_types=[], token_ids=[], redacted_text=text)

    # Sort by start offset descending so replacements don't shift indices
    spans = [
        PiiSpan(
            start=e["BeginOffset"],
            end=e["EndOffset"],
            entity_type=e["Type"],
            score=e["Score"],
        )
        for e in entities
    ]
    spans.sort(key=lambda s: s.start, reverse=True)

    redacted = truncated
    token_ids = []
    token_records = {}

    for span in spans:
        original = truncated[span.start:span.end]
        placeholder = f"[{span.entity_type}:{span.token_id}]"
        redacted = redacted[:span.start] + placeholder + redacted[span.end:]
        token_ids.append(span.token_id)
        token_records[span.token_id] = {
            "entity_type": span.entity_type,
            "original": original,
            "request_id": request_id,
            "score": span.score,
        }

    # Persist token→original mapping in the token store
    bucket = _token_bucket()
    if bucket and token_records:
        s3 = _get_s3()
        key = f"tokens/{request_id}/{uuid.uuid4().hex}.json"
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=json.dumps(token_records).encode(),
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=_token_key_arn(),
            ContentType="application/json",
        )

    entity_types = list({s.entity_type for s in spans})
    return PiiResult(
        detected=True,
        entity_types=entity_types,
        token_ids=token_ids,
        redacted_text=redacted,
    )


def reverse_token(token_id: str, request_id: str) -> Optional[str]:
    """Look up the original value for a PII token. Returns None if not found."""
    if not _token_bucket():
        return None
    s3 = _get_s3()
    prefix = f"tokens/{request_id}/"
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=TOKEN_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            body = s3.get_object(Bucket=TOKEN_BUCKET, Key=obj["Key"])["Body"].read()
            records = json.loads(body)
            if token_id in records:
                return records[token_id]["original"]
    return None
