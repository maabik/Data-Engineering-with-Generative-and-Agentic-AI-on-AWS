"""Stitcher: groups raw RunSpan events by run_id into RunRecord summaries.

Input:  stream of RunSpan dicts (from Firehose S3 prefix, CW Logs, or the
        ai-finops Iceberg table) for a single day.
Output: one RunRecord per run_id — the single row that captures everything
        a user question cost.

RunRecord exposes the hidden cost structure:
  - total_cost_usd          full cost of the turn (all models + embeddings)
  - model_invocations       how many times LLMs were called
  - tool_invocations        Lambda / code interpreter / web calls
  - retrieval_calls         KB / vector search calls
  - embedding_calls         embedding API calls
  - orchestration_cost_usd  cost of the orchestrator model alone
  - retrieval_cost_usd      cost of embedding calls during retrieval
  - model_breakdown         per-model token + cost breakdown
  - cost_amplification      total_cost / cheapest_single_invocation_cost
                            (the "hidden cost" multiplier)
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import Iterable

logger = logging.getLogger(__name__)


@dataclass
class RunRecord:
    run_id: str
    trace_id: str
    user_id: str
    tenant_id: str
    session_id: str
    workload: str
    date: str                          # YYYY-MM-DD of first span
    started_at: str
    ended_at: str
    wall_clock_ms: int                 # ended_at - started_at

    # Span counts
    total_spans: int
    model_invocations: int
    tool_invocations: int
    retrieval_calls: int
    embedding_calls: int
    tool_errors: int
    span_errors: int

    # Token totals
    total_input_tokens: int
    total_output_tokens: int
    total_tokens: int
    total_embedding_tokens: int

    # Cost totals
    total_estimated_cost_usd: float
    orchestration_cost_usd: float      # first model invocation (orchestrator)
    retrieval_cost_usd: float          # embedding calls only
    tool_cost_usd: float               # 0 (tool calls billed separately)
    cost_amplification: float          # total / cheapest_single_model_call

    # Retrieval
    total_retrieved_chunks: int

    # Model breakdown: {model_id: {invocations, input_tokens, output_tokens, cost_usd}}
    model_breakdown: dict = field(default_factory=dict)

    # Spans list (optional, for debugging)
    spans: list[dict] = field(default_factory=list, repr=False)

    def to_dict(self, include_spans: bool = False) -> dict:
        d = asdict(self)
        if not include_spans:
            d.pop("spans", None)
        return d


def _iso_to_ms(iso: str) -> int:
    """Parse ISO datetime string to milliseconds since epoch."""
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def stitch_runs(
    spans: Iterable[dict],
    include_spans: bool = False,
) -> list[RunRecord]:
    """Group spans by run_id and produce one RunRecord per run.

    Spans must have at minimum: run_id, trace_id, span_type, started_at.
    All other fields default gracefully if missing.
    """
    # Group by run_id
    by_run: dict[str, list[dict]] = defaultdict(list)
    for span in spans:
        run_id = span.get("run_id") or span.get("trace_id", "")
        if run_id:
            by_run[run_id].append(span)

    records: list[RunRecord] = []
    for run_id, run_spans in by_run.items():
        # Sort by started_at for wall-clock calculation
        run_spans.sort(key=lambda s: s.get("started_at", ""))

        model_spans = [s for s in run_spans if s.get("span_type") == "model"]
        tool_spans = [s for s in run_spans if s.get("span_type") == "tool"]
        retrieval_spans = [s for s in run_spans if s.get("span_type") == "retrieval"]
        embedding_spans = [s for s in run_spans if s.get("span_type") == "embedding"]

        # Attribution from first available span
        first = run_spans[0]
        user_id = next((s.get("user_id", "") for s in run_spans if s.get("user_id")), "")
        tenant_id = next((s.get("tenant_id", "") for s in run_spans if s.get("tenant_id")), "_unknown")
        session_id = next((s.get("session_id", "") for s in run_spans if s.get("session_id")), "")
        workload = next((s.get("workload", "") for s in run_spans if s.get("workload")), "")
        trace_id = first.get("trace_id", run_id)

        started_at = run_spans[0].get("started_at", "")
        ended_at = run_spans[-1].get("ended_at", "") or run_spans[-1].get("started_at", "")
        started_ms = _iso_to_ms(started_at)
        ended_ms = _iso_to_ms(ended_at)
        wall_ms = max(0, ended_ms - started_ms)

        # Token aggregation (models only)
        total_input = sum(int(s.get("input_tokens", 0)) for s in model_spans)
        total_output = sum(int(s.get("output_tokens", 0)) for s in model_spans)
        total_embed = sum(int(s.get("embedding_input_tokens", 0)) for s in embedding_spans)

        # Cost aggregation
        total_cost = sum(float(s.get("estimated_cost_usd", 0.0)) for s in run_spans)
        embed_cost = sum(float(s.get("estimated_cost_usd", 0.0)) for s in embedding_spans)
        # Orchestration cost = cost of the FIRST model call (the orchestrator LLM)
        orch_cost = float(model_spans[0].get("estimated_cost_usd", 0.0)) if model_spans else 0.0

        # Per-model breakdown
        model_bd: dict[str, dict] = {}
        for s in model_spans:
            mid = s.get("model_id", "unknown")
            if mid not in model_bd:
                model_bd[mid] = {"invocations": 0, "input_tokens": 0,
                                  "output_tokens": 0, "cost_usd": 0.0}
            model_bd[mid]["invocations"] += 1
            model_bd[mid]["input_tokens"] += int(s.get("input_tokens", 0))
            model_bd[mid]["output_tokens"] += int(s.get("output_tokens", 0))
            model_bd[mid]["cost_usd"] += float(s.get("estimated_cost_usd", 0.0))

        # Cost amplification: how many × more expensive than the cheapest single model call
        min_call = min((float(s.get("estimated_cost_usd", 0.0))
                        for s in model_spans if float(s.get("estimated_cost_usd", 0.0)) > 0),
                       default=0.0)
        amplification = round(total_cost / min_call, 2) if min_call > 0 else 0.0

        date_str = started_at[:10] if started_at else ""

        rec = RunRecord(
            run_id=run_id,
            trace_id=trace_id,
            user_id=user_id,
            tenant_id=tenant_id,
            session_id=session_id,
            workload=workload,
            date=date_str,
            started_at=started_at,
            ended_at=ended_at,
            wall_clock_ms=wall_ms,
            total_spans=len(run_spans),
            model_invocations=len(model_spans),
            tool_invocations=len(tool_spans),
            retrieval_calls=len(retrieval_spans),
            embedding_calls=len(embedding_spans),
            tool_errors=sum(1 for s in tool_spans if not s.get("tool_success", True)),
            span_errors=sum(1 for s in run_spans if s.get("error")),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            total_tokens=total_input + total_output,
            total_embedding_tokens=total_embed,
            total_estimated_cost_usd=round(total_cost, 8),
            orchestration_cost_usd=round(orch_cost, 8),
            retrieval_cost_usd=round(embed_cost, 8),
            tool_cost_usd=0.0,
            cost_amplification=amplification,
            total_retrieved_chunks=sum(int(s.get("retrieved_chunks", 0)) for s in retrieval_spans),
            model_breakdown=model_bd,
            spans=run_spans if include_spans else [],
        )
        records.append(rec)

    logger.info("Stitched %d runs from %d spans", len(records),
                sum(len(v) for v in by_run.values()))
    return records
