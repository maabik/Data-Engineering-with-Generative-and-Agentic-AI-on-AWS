"""Tests for tracer/run_tracer.py"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
import uuid
import pytest
from tracer.run_tracer import RunTracer, RunSpan, SpanType, run_context, _estimate, _price


# ── Pricing ───────────────────────────────────────────────────────────────────

class TestPricing:
    def test_sonnet_prices(self):
        inp, out = _price("anthropic.claude-3-sonnet-20240229-v1:0")
        assert inp == 0.003
        assert out == 0.015

    def test_haiku_cheaper(self):
        hi, ho = _price("anthropic.claude-3-haiku-20240307-v1:0")
        si, so = _price("anthropic.claude-3-sonnet-20240229-v1:0")
        assert hi < si

    def test_unknown_zero(self):
        assert _price("unknown.model") == (0.0, 0.0)

    def test_estimate(self):
        cost = _estimate(1000, 500, "anthropic.claude-3-sonnet-20240229-v1:0")
        expected = (1000 / 1000.0) * 0.003 + (500 / 1000.0) * 0.015
        assert abs(cost - expected) < 1e-9


# ── RunTracer basics ──────────────────────────────────────────────────────────

class TestRunTracer:
    def _tracer(self, **kw):
        return RunTracer(user_id="u1", tenant_id="acme", emit_backend="none", **kw)

    def test_trace_id_auto_generated(self):
        t = self._tracer()
        assert t.trace_id
        uuid.UUID(t.trace_id)

    def test_explicit_trace_id(self):
        t = self._tracer(trace_id="my-trace")
        assert t.trace_id == "my-trace"

    def test_run_id_differs_from_trace_id(self):
        t = self._tracer()
        assert t.run_id != t.trace_id

    def test_record_model_span(self):
        t = self._tracer()
        s = t.record_model_span("anthropic.claude-3-haiku-20240307-v1:0", 100, 50, 800)
        assert s.span_type == "model"
        assert s.input_tokens == 100
        assert s.output_tokens == 50
        assert s.total_tokens == 150
        assert s.estimated_cost_usd > 0
        assert len(t.spans) == 1

    def test_record_tool_span(self):
        t = self._tracer()
        s = t.record_tool_span("web_search", 200, success=True)
        assert s.span_type == "tool"
        assert s.tool_name == "web_search"
        assert s.tool_success is True

    def test_record_retrieval_span(self):
        t = self._tracer()
        s = t.record_retrieval_span("kb-001", "what is revenue?", 5, 150)
        assert s.span_type == "retrieval"
        assert s.kb_id == "kb-001"
        assert s.retrieved_chunks == 5
        assert len(t.spans) == 1

    def test_record_embedding_span(self):
        t = self._tracer()
        s = t.record_embedding_span("amazon.titan-embed-text-v1", 200, 50)
        assert s.span_type == "embedding"
        assert s.embedding_input_tokens == 200
        assert s.estimated_cost_usd > 0

    def test_span_context_manager_records(self):
        t = self._tracer()
        with t.span(SpanType.MODEL, model_id="anthropic.claude-3-haiku-20240307-v1:0") as s:
            s.input_tokens = 50
            s.output_tokens = 20
        assert len(t.spans) == 1
        assert t.spans[0].total_tokens == 70

    def test_span_context_manager_records_error(self):
        t = self._tracer()
        with pytest.raises(ValueError):
            with t.span(SpanType.TOOL, tool_name="broken_tool") as s:
                raise ValueError("tool failed")
        # Span still recorded with error
        assert len(t.spans) == 1
        assert "tool failed" in t.spans[0].error

    def test_span_duration_measured(self):
        t = self._tracer()
        with t.span(SpanType.TOOL, tool_name="slow_tool") as s:
            time.sleep(0.05)  # 50ms
        assert t.spans[0].duration_ms >= 40  # allow measurement slack

    def test_attribution_propagated(self):
        t = RunTracer(user_id="alice", tenant_id="globex",
                      session_id="s1", workload="chat", emit_backend="none")
        t.record_tool_span("my_tool", 100)
        s = t.spans[0]
        assert s.user_id == "alice"
        assert s.tenant_id == "globex"
        assert s.workload == "chat"


# ── run_summary ───────────────────────────────────────────────────────────────

class TestRunSummary:
    def _tracer_with_spans(self):
        t = RunTracer(user_id="u1", tenant_id="acme", emit_backend="none")
        t.record_model_span("anthropic.claude-3-sonnet-20240229-v1:0", 500, 200, 1000)
        t.record_model_span("anthropic.claude-3-haiku-20240307-v1:0", 200, 100, 400)
        t.record_tool_span("web_search", 300, success=True)
        t.record_retrieval_span("kb-1", "query", 4, 120)
        t.record_embedding_span("amazon.titan-embed-text-v1", 150, 80)
        return t

    def test_counts(self):
        t = self._tracer_with_spans()
        s = t.run_summary()
        assert s["model_invocations"] == 2
        assert s["tool_invocations"] == 1
        assert s["retrieval_calls"] == 1
        assert s["embedding_calls"] == 1

    def test_total_tokens(self):
        t = self._tracer_with_spans()
        s = t.run_summary()
        assert s["total_input_tokens"] == 700
        assert s["total_output_tokens"] == 300
        assert s["total_tokens"] == 1000

    def test_cost_positive(self):
        t = self._tracer_with_spans()
        s = t.run_summary()
        assert s["total_estimated_cost_usd"] > 0

    def test_model_breakdown(self):
        t = self._tracer_with_spans()
        s = t.run_summary()
        assert "anthropic.claude-3-sonnet-20240229-v1:0" in s["model_breakdown"]
        assert s["model_breakdown"]["anthropic.claude-3-sonnet-20240229-v1:0"]["invocations"] == 1

    def test_embedding_token_count(self):
        t = self._tracer_with_spans()
        s = t.run_summary()
        assert s["total_embedding_tokens"] == 150

    def test_no_spans_summary(self):
        t = RunTracer(emit_backend="none")
        s = t.run_summary()
        assert s["total_spans"] == 0
        assert s["total_estimated_cost_usd"] == 0.0


# ── run_context ───────────────────────────────────────────────────────────────

class TestRunContext:
    def test_context_manager_yields_tracer(self):
        with run_context(user_id="u1", emit_backend="none") as t:
            assert isinstance(t, RunTracer)
            t.record_tool_span("tool1", 100)
        assert len(t.spans) == 1

    def test_trace_id_propagated(self):
        tid = "fixed-trace-id"
        with run_context(trace_id=tid, emit_backend="none") as t:
            assert t.trace_id == tid

    def test_flush_called_on_exit(self, monkeypatch):
        flushed = []
        with run_context(emit_backend="none") as t:
            monkeypatch.setattr(t, "flush", lambda: flushed.append(True))
        assert len(flushed) == 1

    def test_flush_called_even_on_exception(self, monkeypatch):
        flushed = []
        with pytest.raises(RuntimeError):
            with run_context(emit_backend="none") as t:
                monkeypatch.setattr(t, "flush", lambda: flushed.append(True))
                raise RuntimeError("boom")
        assert len(flushed) == 1


# ── emit backend ─────────────────────────────────────────────────────────────

class TestEmitBackend:
    def test_stdout_emit(self, capsys):
        t = RunTracer(emit_backend="stdout")
        t.record_tool_span("tool1", 100)
        captured = capsys.readouterr().out
        assert "tool1" in captured

    def test_none_emit_no_output(self, capsys):
        t = RunTracer(emit_backend="none")
        t.record_tool_span("tool1", 100)
        captured = capsys.readouterr().out
        assert captured == ""

    def test_firehose_emit_calls_boto3(self, monkeypatch):
        captured = []

        class FakeFirehose:
            def put_record(self, DeliveryStreamName, Record):
                captured.append(Record)

        import boto3
        monkeypatch.setattr(
            "boto3.Session.client",
            lambda self, svc, **kw: FakeFirehose() if svc == "firehose" else None,
        )
        t = RunTracer(emit_backend="firehose", firehose_stream="my-stream")
        t.record_tool_span("tool1", 100)
        assert len(captured) == 1
        assert b"tool1" in captured[0]["Data"]
