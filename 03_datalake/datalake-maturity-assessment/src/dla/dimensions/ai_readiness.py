"""AI / Agentic Readiness dimension."""
from __future__ import annotations

from typing import Any


def score(snapshot: dict[str, Any]):
    from . import DimensionScore

    kb = snapshot.get("bedrock_kb", {}) or {}
    s3v = snapshot.get("s3_vectors", {}) or {}
    osearch = snapshot.get("opensearch", {}) or {}
    agentcore = snapshot.get("agentcore", {}) or {}

    has_kb = kb.get("kb_count", 0) > 0
    has_agents = kb.get("agent_count", 0) > 0
    has_vectors = s3v.get("index_count", 0) > 0 or len(osearch.get("vector_collections", []) or []) > 0
    has_agentcore = agentcore.get("gateway_count", 0) > 0 or agentcore.get("runtime_count", 0) > 0

    evidence = [
        f"Bedrock Knowledge Bases: {kb.get('kb_count', 0)} ({kb.get('data_source_count', 0)} data sources)",
        f"Bedrock agents: {kb.get('agent_count', 0)}",
        f"S3 Vectors indexes: {s3v.get('index_count', 0)} (across {s3v.get('vector_bucket_count', 0)} buckets)",
        f"OpenSearch vector collections: {len(osearch.get('vector_collections', []) or [])}",
        f"Bedrock AgentCore: gateways={agentcore.get('gateway_count', 0)}, runtimes={agentcore.get('runtime_count', 0)}",
    ]

    level = 1
    if has_vectors:
        level = 3  # L2 (separate feature store) is hard to detect without SageMaker call; skip to 3 if vectors are real.
    if has_kb and (has_vectors or kb.get("data_source_count", 0) > 0):
        level = 4
    if level >= 4 and has_agentcore:
        level = 5

    if level == 1:
        top_gap = "Bring vector storage online (S3 Vectors or OpenSearch vector engine) over your top tables."
    elif level == 3:
        top_gap = "Connect Bedrock Knowledge Bases to your lake content and start exposing tables via MCP."
    elif level == 4:
        top_gap = "Move agent access to Bedrock AgentCore Gateway with Identity Center, automate embedding refresh."
    else:
        top_gap = "—"

    return DimensionScore(name="ai_readiness", level=level, evidence=evidence, top_gap=top_gap)
