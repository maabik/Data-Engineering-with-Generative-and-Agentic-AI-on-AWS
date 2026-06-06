"""Catalog & Schema Management dimension."""
from __future__ import annotations

from typing import Any


def score(snapshot: dict[str, Any]):
    from . import DimensionScore

    glue = snapshot.get("glue", {}) or {}
    dz = snapshot.get("datazone", {}) or {}

    table_count = glue.get("table_count", 0)
    described = glue.get("described_tables", 0)
    described_share = (described / table_count) if table_count else 0.0
    crawler_count = glue.get("crawler_count", 0)
    dz_active = dz.get("available", False) and dz.get("domain_count", 0) > 0
    dz_assets = dz.get("asset_count", 0)

    evidence = [
        f"Glue tables: {table_count}, with column descriptions on ≥80% of columns: {described} ({described_share:.0%})",
        f"Glue crawlers configured: {crawler_count}",
        f"DataZone: {'active, ' + str(dz.get('domain_count', 0)) + ' domain(s), ' + str(dz_assets) + ' assets' if dz_active else 'no domains detected'}",
    ]

    level = 1
    if table_count > 0 and crawler_count > 0:
        level = 2
    if described_share >= 0.5:
        level = 3
    if dz_active and dz_assets > 0:
        level = 4
    if dz_active and dz_assets >= 50:
        level = 5

    if level == 1:
        top_gap = "Run a Glue crawler against your lake buckets to bootstrap a Data Catalog."
    elif level == 2:
        top_gap = f"{table_count - described} tables lack column descriptions — auto-generate via Bedrock and write back."
    elif level == 3:
        top_gap = "Stand up a DataZone domain to publish curated assets and a business glossary."
    elif level == 4:
        top_gap = "Grow DataZone asset coverage and adopt versioned data contracts."
    else:
        top_gap = "—"

    return DimensionScore(name="catalog_schema", level=level, evidence=evidence, top_gap=top_gap)
