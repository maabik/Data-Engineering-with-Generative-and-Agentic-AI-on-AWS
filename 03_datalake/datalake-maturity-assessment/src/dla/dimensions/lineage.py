"""Lineage & Provenance dimension."""
from __future__ import annotations

from typing import Any


def score(snapshot: dict[str, Any]):
    from . import DimensionScore

    glue = snapshot.get("glue", {}) or {}
    dz = snapshot.get("datazone", {}) or {}

    sigs = glue.get("job_signals", {}) or {}
    crawler_lineage = sigs.get("lineage_config_enabled", 0)
    openlineage = sigs.get("openlineage", 0)
    dz_active = dz.get("available", False) and dz.get("domain_count", 0) > 0

    evidence = [
        f"Glue crawlers with LineageConfiguration=ENABLE: {crawler_lineage}",
        f"OpenLineage / Marquez signals in Glue jobs: {openlineage}",
        f"DataZone: {'active — column-level lineage available in UI' if dz_active else 'no domains'}",
    ]

    level = 1
    if crawler_lineage > 0 or openlineage > 0:
        level = 3
    if dz_active and (openlineage > 0 or crawler_lineage > 0):
        level = 4
    # L5 (end-to-end + agent-queryable lineage) requires MCP server detection — not implemented in v0.1.

    if level == 1:
        top_gap = "Enable Glue Crawler lineage and emit OpenLineage events from Glue jobs and Step Functions."
    elif level == 3:
        top_gap = "Stand up DataZone (or self-managed Marquez) for column-level lineage UX."
    elif level == 4:
        top_gap = "Expose lineage to agents via an MCP server and roll out across BI/agent consumers."
    else:
        top_gap = "—"

    return DimensionScore(name="lineage", level=level, evidence=evidence, top_gap=top_gap)
