"""Tests for stitcher/stitch.py"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from stitcher.stitch import stitch_runs, RunRecord


def _model_span(run_id, model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                input_tokens=500, output_tokens=200, cost=1.5,
                started="2024-01-15T10:00:00Z", ended="2024-01-15T10:00:01Z",
                **kw):
    return {
        "run_id": run_id, "trace_id": run_id,
        "span_type": "model", "model_id": model_id,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "estimated_cost_usd": cost,
        "started_at": started, "ended_at": ended,
        "user_id": kw.get("user_id", "u1"),
        "tenant_id": kw.get("tenant_id", "acme"),
        "session_id": kw.get("session_id", "s1"),
        "workload": kw.get("workload", "chat"),
    }


def _tool_span(run_id, tool_name="web_search", success=True, started="2024-01-15T10:00:01Z",
               ended="2024-01-15T10:00:02Z", **kw):
    return {
        "run_id": run_id, "trace_id": run_id,
        "span_type": "tool", "tool_name": tool_name,
        "tool_success": success, "estimated_cost_usd": 0.0,
        "started_at": started, "ended_at": ended,
        "user_id": kw.get("user_id", "u1"),
        "tenant_id": kw.get("tenant_id", "acme"),
    }


def _retrieval_span(run_id, kb_id="kb-1", chunks=5,
                    started="2024-01-15T10:00:02Z", ended="2024-01-15T10:00:03Z", **kw):
    return {
        "run_id": run_id, "trace_id": run_id,
        "span_type": "retrieval", "kb_id": kb_id,
        "retrieved_chunks": chunks, "estimated_cost_usd": 0.0,
        "started_at": started, "ended_at": ended,
        "user_id": kw.get("user_id", "u1"),
        "tenant_id": kw.get("tenant_id", "acme"),
    }


def _embedding_span(run_id, model="amazon.titan-embed-text-v1", tokens=200, cost=0.004,
                    started="2024-01-15T10:00:03Z", ended="2024-01-15T10:00:04Z", **kw):
    return {
        "run_id": run_id, "trace_id": run_id,
        "span_type": "embedding", "embedding_model": model,
        "embedding_input_tokens": tokens, "estimated_cost_usd": cost,
        "started_at": started, "ended_at": ended,
        "user_id": kw.get("user_id", "u1"),
        "tenant_id": kw.get("tenant_id", "acme"),
    }


# ── Basic grouping ────────────────────────────────────────────────────────────

class TestStitchBasic:
    def test_empty_input(self):
        assert stitch_runs([]) == []

    def test_single_model_span(self):
        runs = stitch_runs([_model_span("run-1")])
        assert len(runs) == 1
        r = runs[0]
        assert r.run_id == "run-1"
        assert r.model_invocations == 1
        assert r.total_spans == 1

    def test_two_runs_separated(self):
        spans = [_model_span("run-1"), _model_span("run-2")]
        runs = stitch_runs(spans)
        assert len(runs) == 2
        ids = {r.run_id for r in runs}
        assert ids == {"run-1", "run-2"}

    def test_multiple_spans_same_run(self):
        spans = [
            _model_span("run-1", started="2024-01-15T10:00:00Z"),
            _tool_span("run-1", started="2024-01-15T10:00:01Z"),
            _retrieval_span("run-1", started="2024-01-15T10:00:02Z"),
        ]
        runs = stitch_runs(spans)
        assert len(runs) == 1
        r = runs[0]
        assert r.total_spans == 3
        assert r.model_invocations == 1
        assert r.tool_invocations == 1
        assert r.retrieval_calls == 1

    def test_span_without_run_id_uses_trace_id(self):
        span = {
            "trace_id": "trace-99",
            "span_type": "tool", "tool_name": "t",
            "estimated_cost_usd": 0.0,
            "started_at": "2024-01-15T10:00:00Z", "ended_at": "2024-01-15T10:00:01Z",
        }
        runs = stitch_runs([span])
        assert len(runs) == 1
        assert runs[0].run_id == "trace-99"


# ── Token and cost aggregation ────────────────────────────────────────────────

class TestAggregation:
    def _multi_span_run(self):
        return [
            _model_span("r1", model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                        input_tokens=500, output_tokens=200, cost=1.5,
                        started="2024-01-15T10:00:00Z"),
            _model_span("r1", model_id="anthropic.claude-3-haiku-20240307-v1:0",
                        input_tokens=200, output_tokens=100, cost=0.5,
                        started="2024-01-15T10:00:01Z"),
            _embedding_span("r1", tokens=150, cost=0.003,
                            started="2024-01-15T10:00:02Z"),
            _tool_span("r1", started="2024-01-15T10:00:03Z"),
        ]

    def test_total_input_tokens(self):
        r = stitch_runs(self._multi_span_run())[0]
        assert r.total_input_tokens == 700

    def test_total_output_tokens(self):
        r = stitch_runs(self._multi_span_run())[0]
        assert r.total_output_tokens == 300

    def test_total_tokens(self):
        r = stitch_runs(self._multi_span_run())[0]
        assert r.total_tokens == 1000

    def test_embedding_tokens_separate(self):
        r = stitch_runs(self._multi_span_run())[0]
        assert r.total_embedding_tokens == 150

    def test_total_cost_includes_all_spans(self):
        r = stitch_runs(self._multi_span_run())[0]
        assert abs(r.total_estimated_cost_usd - 2.003) < 1e-6

    def test_embedding_cost_is_retrieval_cost_usd(self):
        r = stitch_runs(self._multi_span_run())[0]
        assert abs(r.retrieval_cost_usd - 0.003) < 1e-6

    def test_orchestration_cost_is_first_model_span(self):
        r = stitch_runs(self._multi_span_run())[0]
        assert abs(r.orchestration_cost_usd - 1.5) < 1e-6


# ── Cost amplification ────────────────────────────────────────────────────────

class TestCostAmplification:
    def test_amplification_formula(self):
        spans = [
            _model_span("r1", cost=0.10, started="2024-01-15T10:00:00Z"),
            _model_span("r1", cost=0.10, started="2024-01-15T10:00:01Z"),
            _embedding_span("r1", cost=0.01, started="2024-01-15T10:00:02Z"),
        ]
        r = stitch_runs(spans)[0]
        # total_cost=0.21, min_model_call=0.10 → amp=2.10
        assert abs(r.cost_amplification - 2.10) < 0.01

    def test_single_model_call_amplification_is_one(self):
        r = stitch_runs([_model_span("r1", cost=0.05)])[0]
        assert r.cost_amplification == pytest.approx(1.0, rel=0.01)

    def test_zero_cost_amplification_is_zero(self):
        # all spans have zero cost
        spans = [_tool_span("r1")]
        r = stitch_runs(spans)[0]
        assert r.cost_amplification == 0.0

    def test_high_amplification(self):
        # 10 model calls at $0.10 each + overhead → amp ~10+
        spans = [_model_span("r1", cost=0.10,
                              started=f"2024-01-15T10:{m:02d}:00Z")
                 for m in range(10)]
        r = stitch_runs(spans)[0]
        assert r.cost_amplification >= 9.9


# ── Model breakdown ───────────────────────────────────────────────────────────

class TestModelBreakdown:
    def test_breakdown_keys(self):
        spans = [
            _model_span("r1", model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                        cost=1.0, started="2024-01-15T10:00:00Z"),
            _model_span("r1", model_id="anthropic.claude-3-haiku-20240307-v1:0",
                        cost=0.5, started="2024-01-15T10:00:01Z"),
        ]
        r = stitch_runs(spans)[0]
        assert "anthropic.claude-3-sonnet-20240229-v1:0" in r.model_breakdown
        assert "anthropic.claude-3-haiku-20240307-v1:0" in r.model_breakdown

    def test_breakdown_invocation_count(self):
        spans = [
            _model_span("r1", model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                        started="2024-01-15T10:00:00Z"),
            _model_span("r1", model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                        started="2024-01-15T10:00:01Z"),
        ]
        r = stitch_runs(spans)[0]
        assert r.model_breakdown["anthropic.claude-3-sonnet-20240229-v1:0"]["invocations"] == 2

    def test_breakdown_token_sum(self):
        spans = [
            _model_span("r1", model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                        input_tokens=300, output_tokens=100, started="2024-01-15T10:00:00Z"),
            _model_span("r1", model_id="anthropic.claude-3-sonnet-20240229-v1:0",
                        input_tokens=200, output_tokens=50, started="2024-01-15T10:00:01Z"),
        ]
        r = stitch_runs(spans)[0]
        bd = r.model_breakdown["anthropic.claude-3-sonnet-20240229-v1:0"]
        assert bd["input_tokens"] == 500
        assert bd["output_tokens"] == 150


# ── Wall clock ────────────────────────────────────────────────────────────────

class TestWallClock:
    def test_wall_clock_ms_from_timestamps(self):
        spans = [
            _model_span("r1",
                        started="2024-01-15T10:00:00.000Z",
                        ended="2024-01-15T10:00:00.000Z"),
            _tool_span("r1",
                       started="2024-01-15T10:00:00.500Z",
                       ended="2024-01-15T10:00:02.000Z"),
        ]
        r = stitch_runs(spans)[0]
        # started_at is first span start; ended_at is last span end
        assert r.wall_clock_ms >= 2000

    def test_wall_clock_zero_for_same_timestamps(self):
        spans = [_model_span("r1",
                              started="2024-01-15T10:00:00Z",
                              ended="2024-01-15T10:00:00Z")]
        r = stitch_runs(spans)[0]
        assert r.wall_clock_ms == 0

    def test_date_from_started_at(self):
        r = stitch_runs([_model_span("r1", started="2024-01-15T10:00:00Z")])[0]
        assert r.date == "2024-01-15"


# ── Attribution ───────────────────────────────────────────────────────────────

class TestAttribution:
    def test_attribution_from_spans(self):
        spans = [_model_span("r1", user_id="alice", tenant_id="globex",
                              session_id="sess-1", workload="chat")]
        r = stitch_runs(spans)[0]
        assert r.user_id == "alice"
        assert r.tenant_id == "globex"
        assert r.session_id == "sess-1"
        assert r.workload == "chat"

    def test_unknown_tenant_default(self):
        span = {
            "run_id": "r1", "trace_id": "r1",
            "span_type": "tool", "tool_name": "t",
            "estimated_cost_usd": 0.0,
            "started_at": "2024-01-15T10:00:00Z",
            "ended_at": "2024-01-15T10:00:01Z",
        }
        r = stitch_runs([span])[0]
        assert r.tenant_id == "_unknown"


# ── Error counting ────────────────────────────────────────────────────────────

class TestErrorCounting:
    def test_tool_errors_counted(self):
        spans = [
            _tool_span("r1", success=False, started="2024-01-15T10:00:00Z"),
            _tool_span("r1", success=True, started="2024-01-15T10:00:01Z"),
        ]
        r = stitch_runs(spans)[0]
        assert r.tool_errors == 1

    def test_span_errors_counted(self):
        spans = [
            {**_model_span("r1"), "error": "timeout"},
            _tool_span("r1"),
        ]
        r = stitch_runs(spans)[0]
        assert r.span_errors == 1


# ── Span inclusion ────────────────────────────────────────────────────────────

class TestSpanInclusion:
    def test_spans_excluded_by_default(self):
        r = stitch_runs([_model_span("r1")])[0]
        assert r.spans == []

    def test_spans_included_when_requested(self):
        r = stitch_runs([_model_span("r1")], include_spans=True)[0]
        assert len(r.spans) == 1

    def test_to_dict_excludes_spans_by_default(self):
        r = stitch_runs([_model_span("r1")])[0]
        d = r.to_dict()
        assert "spans" not in d

    def test_to_dict_includes_spans_when_requested(self):
        r = stitch_runs([_model_span("r1")], include_spans=True)[0]
        d = r.to_dict(include_spans=True)
        assert "spans" in d
