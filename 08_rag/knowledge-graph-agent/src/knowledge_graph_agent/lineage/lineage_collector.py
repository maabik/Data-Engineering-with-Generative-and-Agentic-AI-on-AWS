"""Subscribe to OpenLineage events from a queue/file and hand them off
to ``lineage_to_graph`` for projection into the graph.

This module is intentionally minimal — production deployments push
OpenLineage events through Marquez, EventBridge, or Kinesis. The
collector here accepts events as a list of dicts so it stays unit-
testable without external infra.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


def load_events(source: str | Path | Iterable[dict]) -> list[dict]:
    """Read OpenLineage events from a JSON-lines file or pass-through an iterable."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return list(source)
