"""Data Quality & Observability dimension."""
from __future__ import annotations

from typing import Any


def score(snapshot: dict[str, Any]):
    from . import DimensionScore

    glue = snapshot.get("glue", {}) or {}
    sigs = glue.get("job_signals", {}) or {}

    deequ = sigs.get("deequ", 0)
    ge = sigs.get("great_expectations", 0)
    job_count = max(1, glue.get("job_count", 0))
    qa_share = (deequ + ge) / job_count

    evidence = [
        f"Glue jobs: {glue.get('job_count', 0)} — Deequ signals: {deequ}, Great Expectations signals: {ge} (≈{qa_share:.0%} of jobs)",
    ]

    level = 1
    if glue.get("job_count", 0) > 0:
        level = 2
    if qa_share >= 0.5:
        level = 3
    # L4 (freshness SLOs) and L5 (auto-remediation) require CloudWatch + EventBridge
    # signals not collected in v0.1 — leaves room to upgrade later.

    if level == 1:
        top_gap = "Add automated quality checks (Deequ or Great Expectations) to ingestion pipelines."
    elif level == 2:
        top_gap = "No quality library detected in Glue jobs — wire in Deequ/GE for ≥50% of pipelines."
    elif level == 3:
        top_gap = "Declare freshness SLOs and add CloudWatch anomaly alarms for critical tables."
    else:
        top_gap = "—"

    return DimensionScore(name="quality_observability", level=level, evidence=evidence, top_gap=top_gap)
