from __future__ import annotations

import datetime as _dt
import hashlib
from pathlib import Path
from typing import Iterable, List, Optional

from .chunking import Chunk, chunk_text
from .config import Config
from .embeddings import TitanEmbedder
from .loaders import iter_files, load_file
from .logging_setup import get_logger
from .store import S3VectorStore, VectorRecord

log = get_logger("ingest")


def _stable_key(source: str, locator: str, chunk_idx: int, text: str) -> str:
    digest = hashlib.sha1(f"{source}|{locator}|{chunk_idx}|{text}".encode("utf-8")).hexdigest()[:16]
    return f"{Path(source).stem}-{chunk_idx}-{digest}"


def build_chunks(
    paths: Iterable[Path],
    config: Config,
) -> List[Chunk]:
    """Walk paths, load + section + chunk into uniform Chunk records with metadata."""
    chunks: List[Chunk] = []
    ingest_ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
    for root in paths:
        for fp in iter_files(root):
            sections = load_file(fp)
            log.info("loaded %s -> %d section(s)", fp, len(sections))
            source_value = config.source_tag or fp.name
            for sec in sections:
                base_md = {
                    "source": source_value,
                    "path": str(fp),
                    "locator": sec.locator,
                    "ingest_ts": ingest_ts,
                }
                for ch in chunk_text(
                    sec.text,
                    chunk_size=config.chunk_size,
                    chunk_overlap=config.chunk_overlap,
                    base_metadata=base_md,
                ):
                    chunks.append(ch)
    return chunks


def ingest_paths(
    paths: Iterable[Path],
    config: Config,
    store: Optional[S3VectorStore] = None,
    embedder: Optional[TitanEmbedder] = None,
) -> int:
    """Load -> chunk -> embed -> upsert. Returns the number of vectors written."""
    store = store or S3VectorStore(config)
    embedder = embedder or TitanEmbedder(config)

    store.ensure()

    chunks = build_chunks(list(paths), config)
    if not chunks:
        log.warning("no chunks produced; nothing to ingest")
        return 0
    log.info("prepared %d chunk(s) for embedding", len(chunks))

    vectors = embedder.embed_many(c.text for c in chunks)

    records: List[VectorRecord] = []
    for ch, vec in zip(chunks, vectors):
        md = dict(ch.metadata)
        # Persist a short text preview so retrieval results are human-readable without
        # re-fetching the source. Keep it bounded so we stay well under metadata limits.
        md["text"] = ch.text[:1500]
        key = _stable_key(md.get("source", "unknown"), md.get("locator", ""), md.get("chunk_idx", 0), ch.text)
        records.append(VectorRecord(key=key, vector=vec, metadata=md))

    return store.put_vectors(records)
