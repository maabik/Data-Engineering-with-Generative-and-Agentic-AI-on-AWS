"""Payload loading and schema validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator


_VALID_DETECTOR_TYPES = {"substring", "regex", "canary", "refusal"}
_VALID_SEVERITIES = {"none", "low", "medium", "high", "critical"}


@dataclass
class Payload:
    id: str
    category: str
    severity: str
    technique: str
    user_input: str
    detectors: list[dict[str, Any]]
    tags: list[str] = field(default_factory=list)
    expected: str = "fail_if_triggered"
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any], source: str = "") -> "Payload":
        _validate(d, source)
        return cls(
            id=d["id"],
            category=d["category"],
            severity=d["severity"],
            technique=d.get("technique", ""),
            user_input=d["user_input"],
            detectors=d["detectors"],
            tags=list(d.get("tags", [])),
            expected=d.get("expected", "fail_if_triggered"),
            raw=d,
        )


def _validate(d: dict[str, Any], source: str) -> None:
    for field_name in ("id", "category", "severity", "user_input", "detectors"):
        if field_name not in d:
            raise ValueError(f"{source}: payload missing required field '{field_name}': {d!r}")
    if d["severity"] not in _VALID_SEVERITIES:
        raise ValueError(f"{source}: payload {d['id']} has invalid severity {d['severity']!r}")
    if not isinstance(d["detectors"], list) or not d["detectors"]:
        raise ValueError(f"{source}: payload {d['id']} must have non-empty 'detectors' list")
    for det in d["detectors"]:
        if not isinstance(det, dict) or "type" not in det:
            raise ValueError(f"{source}: payload {d['id']} detector missing 'type': {det!r}")
        if det["type"] not in _VALID_DETECTOR_TYPES:
            raise ValueError(
                f"{source}: payload {d['id']} has unknown detector type {det['type']!r} "
                f"(valid: {sorted(_VALID_DETECTOR_TYPES)})"
            )


def load_payloads(
    paths: Iterable[str | Path] | None = None,
    *,
    categories: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    severities: Iterable[str] | None = None,
    ids: Iterable[str] | None = None,
) -> list[Payload]:
    """Load payloads from one or more .jsonl files or directories.

    Applies optional filters on category, tag, severity, or explicit id list.
    Duplicate ids (across files) raise ValueError.
    """
    if paths is None:
        paths = [Path(__file__).parent.parent / "payloads"]

    jsonl_files: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            jsonl_files.extend(sorted(p.glob("*.jsonl")))
        elif p.is_file():
            jsonl_files.append(p)
        else:
            raise FileNotFoundError(f"Payload path not found: {p}")

    cat_filter = set(categories) if categories else None
    tag_filter = set(tags) if tags else None
    sev_filter = set(severities) if severities else None
    id_filter = set(ids) if ids else None

    payloads: list[Payload] = []
    seen_ids: set[str] = set()
    for path in jsonl_files:
        for lineno, payload in _iter_jsonl(path):
            if payload.id in seen_ids:
                raise ValueError(f"duplicate payload id {payload.id!r} (second hit at {path}:{lineno})")
            seen_ids.add(payload.id)
            if cat_filter and payload.category not in cat_filter:
                continue
            if tag_filter and not (set(payload.tags) & tag_filter):
                continue
            if sev_filter and payload.severity not in sev_filter:
                continue
            if id_filter and payload.id not in id_filter:
                continue
            payloads.append(payload)

    return payloads


def _iter_jsonl(path: Path) -> Iterator[tuple[int, Payload]]:
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {e}") from e
            yield lineno, Payload.from_dict(d, source=f"{path}:{lineno}")
