"""Performance & Ingestion dimension."""
from __future__ import annotations

from typing import Any


def score(snapshot: dict[str, Any]):
    from . import DimensionScore

    glue = snapshot.get("glue", {}) or {}
    athena = snapshot.get("athena", {}) or {}
    s3t = snapshot.get("s3tables", {}) or {}

    triggers = glue.get("trigger_count", 0)
    sched = glue.get("scheduled_trigger_count", 0)
    partitioned = glue.get("partitioned_tables", 0)
    table_count = glue.get("table_count", 0)
    mv_signals = athena.get("materialized_view_signals", 0)
    sampled = max(1, athena.get("sampled_query_count", 0))
    filtered_share = athena.get("queries_with_filters", 0) / sampled

    evidence = [
        f"Glue triggers: {triggers} ({sched} scheduled)",
        f"Partitioned tables: {partitioned} / {table_count}",
        f"Athena queries with filters/partitions: {filtered_share:.0%} (sampled {sampled} queries)",
        f"S3 Tables managed Iceberg: {'yes' if s3t.get('table_count', 0) > 0 else 'no'}",
    ]

    level = 1
    if sched > 0 and partitioned > 0:
        level = 2
    if level >= 2 and filtered_share >= 0.5:
        level = 3
    if level >= 3 and (s3t.get("table_count", 0) > 0 or sched >= 3):
        level = 4
    if level >= 4 and mv_signals > 0:
        level = 5

    if level == 1:
        top_gap = "Add scheduled batch ingestion (Glue/MWAA) and partition the largest tables."
    elif level == 2:
        top_gap = "Add CDC or incremental ingestion (DMS/Kinesis) and ensure queries actually prune partitions."
    elif level == 3:
        top_gap = "Add a streaming path (Firehose → Iceberg/S3 Tables) for hot data."
    elif level == 4:
        top_gap = "Add materialized views and result reuse for top query patterns."
    else:
        top_gap = "—"

    return DimensionScore(name="performance_ingestion", level=level, evidence=evidence, top_gap=top_gap)
