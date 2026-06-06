from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Config:
    region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "us-east-1"))
    bucket_name: str = field(default_factory=lambda: os.environ.get("S3V_BUCKET", "s3-vectors-rag-demo"))
    index_name: str = field(default_factory=lambda: os.environ.get("S3V_INDEX", "rag-index"))
    embed_model_id: str = field(default_factory=lambda: os.environ.get("S3V_EMBED_MODEL", "amazon.titan-embed-text-v2:0"))
    # Titan v2 supports 256/512/1024; default to 1024 to match the index dimension.
    embed_dimension: int = field(default_factory=lambda: int(os.environ.get("S3V_EMBED_DIM", "1024")))
    # Token-aware chunker settings (token counts, not characters).
    chunk_size: int = field(default_factory=lambda: int(os.environ.get("S3V_CHUNK_SIZE", "400")))
    chunk_overlap: int = field(default_factory=lambda: int(os.environ.get("S3V_CHUNK_OVERLAP", "60")))
    # Embedding + put_vectors batching.
    embed_batch_size: int = field(default_factory=lambda: int(os.environ.get("S3V_EMBED_BATCH", "16")))
    put_batch_size: int = field(default_factory=lambda: int(os.environ.get("S3V_PUT_BATCH", "25")))
    distance_metric: str = field(default_factory=lambda: os.environ.get("S3V_DISTANCE", "COSINE"))
    log_level: str = field(default_factory=lambda: os.environ.get("S3V_LOG_LEVEL", "INFO"))
    # When set, ingest will tag every chunk with this source value (overrides filename).
    source_tag: Optional[str] = None
