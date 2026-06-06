from __future__ import annotations

import json
import time
from typing import Iterable, List, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from .config import Config
from .logging_setup import get_logger

log = get_logger("embeddings")


_RETRYABLE_ERROR_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "ModelStreamErrorException",
    "InternalServerException",
}


class TitanEmbedder:
    """Bedrock Titan v2 text embedder with batching and bounded retries."""

    def __init__(self, config: Config, client=None, max_retries: int = 4, base_delay: float = 0.5):
        self._cfg = config
        self._max_retries = max_retries
        self._base_delay = base_delay
        if client is not None:
            self._client = client
        else:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=config.region,
                config=BotoConfig(retries={"max_attempts": 3, "mode": "standard"}),
            )

    def embed_one(self, text: str) -> List[float]:
        body = json.dumps({"inputText": text, "dimensions": self._cfg.embed_dimension})
        attempt = 0
        while True:
            try:
                resp = self._client.invoke_model(modelId=self._cfg.embed_model_id, body=body)
                payload = json.loads(resp["body"].read())
                vec = payload.get("embedding")
                if not vec:
                    raise RuntimeError(f"Bedrock returned no embedding for model {self._cfg.embed_model_id}")
                return vec
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in _RETRYABLE_ERROR_CODES and attempt < self._max_retries:
                    delay = self._base_delay * (2 ** attempt)
                    log.warning("retryable embed error (%s), sleeping %.2fs (attempt %d)", code, delay, attempt + 1)
                    time.sleep(delay)
                    attempt += 1
                    continue
                raise

    def embed_many(self, texts: Iterable[str]) -> List[List[float]]:
        # Bedrock InvokeModel for Titan embeddings is single-input; we batch only at the
        # caller level so we can give progress logs and bounded concurrency. We keep this
        # synchronous to stay simple — switch to a thread pool if you need throughput.
        out: List[List[float]] = []
        batch = self._cfg.embed_batch_size
        items = list(texts)
        for i in range(0, len(items), batch):
            window = items[i : i + batch]
            for j, t in enumerate(window):
                out.append(self.embed_one(t))
            log.info("embedded %d / %d", min(i + batch, len(items)), len(items))
        return out
