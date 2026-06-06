"""Orchestrates the full scan: collectors → snapshot → dimensions → scoring → remediation."""
from __future__ import annotations

import logging
from typing import Any

from rich.console import Console

from . import collectors as c
from .dimensions import ALL_DIMENSIONS
from .remediation import build_remediation_backlog
from .scoring import aggregate, load_weights

log = logging.getLogger(__name__)


_COLLECTOR_STEPS = [
    ("s3", "Collecting S3 inventory and bucket settings", c.collect_s3),
    ("glue", "Inspecting Glue Data Catalog, jobs, and crawlers", c.collect_glue),
    ("lakeformation", "Checking Lake Formation grants and LF-Tags", c.collect_lakeformation),
    ("s3tables", "Probing S3 Tables (managed Iceberg)", c.collect_s3tables),
    ("kms", "Auditing KMS key inventory", c.collect_kms),
    ("athena", "Sampling Athena workgroups and query history", c.collect_athena),
    ("bedrock_kb", "Listing Bedrock Knowledge Bases and Agents", c.collect_bedrock_kb),
    ("s3_vectors", "Probing S3 Vectors", c.collect_s3_vectors),
    ("opensearch", "Listing OpenSearch domains/collections", c.collect_opensearch),
    ("datazone", "Probing Amazon DataZone", c.collect_datazone),
    ("agentcore", "Probing Bedrock AgentCore", c.collect_agentcore),
    ("cost_explorer", "Reading Cost Explorer cost allocation tags and anomaly subscriptions", c.collect_cost_explorer),
    ("cloudtrail", "Listing CloudTrail trails", c.collect_cloudtrail),
]


def run_scan(
    region: str,
    profile: str | None = None,
    preset: str | None = None,
    target_level: int = 4,
    console: Console | None = None,
) -> dict[str, Any]:
    console = console or Console()
    snapshot: dict[str, Any] = {}
    for key, msg, fn in _COLLECTOR_STEPS:
        console.print(f"  → {msg} ...", end="")
        try:
            snapshot[key] = fn(region=region, profile=profile)
            console.print(" [green]done[/green]")
        except Exception as e:
            log.warning("collector %s failed: %s", key, e)
            snapshot[key] = {"error": str(e)}
            console.print(f" [yellow]partial ({type(e).__name__})[/yellow]")

    # Score each dimension
    dim_scores = []
    for _, fn in ALL_DIMENSIONS:
        dim_scores.append(fn(snapshot))

    weights = load_weights(preset)
    summary = aggregate(dim_scores, weights)

    backlog = build_remediation_backlog(summary["scores_by_dimension"], target_level=target_level)

    return {"snapshot": snapshot, "summary": summary, "backlog": backlog, "weights": weights}
