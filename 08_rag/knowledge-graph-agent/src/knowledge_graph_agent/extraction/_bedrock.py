"""Shared Bedrock Converse-API helpers used by the extractors and embedder.

Centralizes the client, retry policy, and token-usage logging so the
extractor modules stay narrow and the agent's behavior is predictable.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import boto3
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from knowledge_graph_agent.config import get_settings

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent / "prompts"


class BedrockExtractionError(RuntimeError):
    """Raised when Bedrock returns an unparseable response."""


def load_prompt(name: str) -> str:
    """Read a prompt template by name (without extension)."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def _bedrock_client():
    return boto3.client("bedrock-runtime", region_name=get_settings().region)


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, max=20),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
def converse(prompt: str, *, model_id: str | None = None) -> tuple[str, dict[str, int]]:
    """Run a Bedrock Converse call with the configured Claude model.

    Returns (response_text, token_usage). Token usage shape:
    ``{"input": int, "output": int, "total": int}``.
    """
    settings = get_settings()
    client = _bedrock_client()
    response = client.converse(
        modelId=model_id or settings.extraction_model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={
            "temperature": settings.extraction_temperature,
            "maxTokens": settings.extraction_max_tokens,
        },
    )
    try:
        text = response["output"]["message"]["content"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise BedrockExtractionError(f"unexpected Converse response: {response!r}") from exc
    usage = response.get("usage", {})
    token_usage = {
        "input": int(usage.get("inputTokens", 0)),
        "output": int(usage.get("outputTokens", 0)),
        "total": int(usage.get("totalTokens", 0)),
    }
    log.info("bedrock.converse tokens=%s", token_usage)
    return text, token_usage


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_response(text: str) -> dict[str, Any]:
    """Best-effort JSON extraction. Strips fences if Claude added them."""
    text = text.strip()
    match = _JSON_FENCE.search(text)
    if match:
        text = match.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to recover by cutting to the outermost braces.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise BedrockExtractionError(f"could not parse JSON from response: {text[:500]!r}") from exc
        raise BedrockExtractionError(f"no JSON object in response: {text[:500]!r}")


@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, max=20),
    reraise=True,
)
def embed(
    text: str,
    *,
    model_id: str | None = None,
    purpose: str = "GENERIC_INDEX",
) -> list[float]:
    """Invoke Amazon Nova 2 Multimodal Embeddings on a text input.

    ``purpose`` is the Nova ``embeddingPurpose``: ``GENERIC_INDEX`` for
    documents you'll embed once and recall against many queries,
    ``TEXT_RETRIEVAL`` for the query side. Defaults to GENERIC_INDEX
    so the dataroom-scale ingest is cheap and consistent.
    """
    settings = get_settings()
    client = _bedrock_client()
    body = {
        "taskType": "SINGLE_EMBEDDING",
        "singleEmbeddingParams": {
            "embeddingPurpose": purpose,
            "text": {"value": text, "truncationMode": "END"},
            "embeddingDimension": settings.embedding_dimensions,
        },
    }
    response = client.invoke_model(
        modelId=model_id or settings.embedding_model_id,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())
    embeddings = payload.get("embeddings")
    if not embeddings:
        raise BedrockExtractionError(f"no embeddings in Nova response: {payload!r}")
    first = embeddings[0]
    if isinstance(first, dict) and "embedding" in first:
        return list(first["embedding"])
    # Defensive: a future Nova schema variant could return raw vectors.
    if isinstance(first, list):
        return list(first)
    raise BedrockExtractionError(f"unexpected Nova embeddings shape: {payload!r}")
