from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Optional


_WORD_RE = re.compile(r"\S+")


def _whitespace_tokenize(text: str) -> List[str]:
    return _WORD_RE.findall(text)


def _try_tiktoken_tokenize(text: str) -> Optional[List[int]]:
    try:
        import tiktoken  # type: ignore
    except Exception:
        return None
    enc = tiktoken.get_encoding("cl100k_base")
    return enc.encode(text)


def _try_tiktoken_decode(tokens: List[int]) -> Optional[str]:
    try:
        import tiktoken  # type: ignore
    except Exception:
        return None
    enc = tiktoken.get_encoding("cl100k_base")
    return enc.decode(tokens)


@dataclass
class Chunk:
    text: str
    metadata: dict = field(default_factory=dict)


def chunk_text(
    text: str,
    chunk_size: int = 400,
    chunk_overlap: int = 60,
    base_metadata: Optional[dict] = None,
) -> List[Chunk]:
    """
    Split text into overlapping chunks of approximately `chunk_size` tokens.

    Uses tiktoken (cl100k_base) when available, otherwise falls back to a
    whitespace tokenizer so the package works without optional dependencies.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be in [0, chunk_size)")

    text = text.strip()
    if not text:
        return []

    base = dict(base_metadata or {})
    chunks: List[Chunk] = []

    tokens = _try_tiktoken_tokenize(text)
    if tokens is not None:
        step = chunk_size - chunk_overlap
        for idx, start in enumerate(range(0, len(tokens), step)):
            window = tokens[start : start + chunk_size]
            if not window:
                break
            decoded = _try_tiktoken_decode(window) or ""
            decoded = decoded.strip()
            if not decoded:
                continue
            md = dict(base)
            md["chunk_idx"] = idx
            chunks.append(Chunk(text=decoded, metadata=md))
            if start + chunk_size >= len(tokens):
                break
        return chunks

    # Whitespace fallback: chunk_size/overlap measured in words.
    words = _whitespace_tokenize(text)
    step = max(chunk_size - chunk_overlap, 1)
    for idx, start in enumerate(range(0, len(words), step)):
        window = words[start : start + chunk_size]
        if not window:
            break
        md = dict(base)
        md["chunk_idx"] = idx
        chunks.append(Chunk(text=" ".join(window), metadata=md))
        if start + chunk_size >= len(words):
            break
    return chunks
