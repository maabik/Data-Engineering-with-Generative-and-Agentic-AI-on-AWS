"""Live integration tests for agent-run-cost-tracer.

Requires: AWS credentials with bedrock-runtime access in us-west-2.
No deployed infrastructure needed — RunTracer calls Bedrock directly
and emits to stdout (no Firehose / CloudWatch in these tests).
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from tracer.run_tracer import RunTracer, SpanType, run_context

REGION = "us-west-2"
MODEL  = "us.amazon.nova-micro-v1:0"   # cheapest; cross-region inference profile


# ── RunTracer: single converse span ──────────────────────────────────────────

def test_tracer_records_model_span():
    t = RunTracer(user_id="live-u1", tenant_id="live-t1", region=REGION,
                  emit_backend="stdout")
    resp = t.converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": "Reply with one word: OK"}]}],
        inferenceConfig={"maxTokens": 10},
    )
    assert resp["stopReason"] in ("max_tokens", "end_turn")
    assert len(t.spans) == 1
    span = t.spans[0]
    assert span.span_type == SpanType.MODEL.value
    assert span.input_tokens > 0
    assert span.output_tokens > 0
    assert span.model_id == MODEL


def test_tracer_injects_request_metadata():
    t = RunTracer(user_id="live-u2", tenant_id="live-t2", region=REGION,
                  emit_backend="stdout")
    # We can't inspect the wire payload, but we verify converse() succeeds
    # with metadata injected (no ValidationException)
    resp = t.converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": "Say 1"}]}],
        inferenceConfig={"maxTokens": 5},
    )
    assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200


def test_tracer_cost_is_positive():
    t = RunTracer(user_id="live-u3", tenant_id="live-t3", region=REGION,
                  emit_backend="stdout")
    t.converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": "Say hello"}]}],
        inferenceConfig={"maxTokens": 10},
    )
    summary = t.run_summary()
    assert summary["total_estimated_cost_usd"] > 0.0
    assert summary["total_input_tokens"] > 0
    assert summary["total_output_tokens"] > 0


def test_tracer_multiple_model_spans_accumulate():
    t = RunTracer(user_id="live-u4", tenant_id="live-t4", region=REGION,
                  emit_backend="stdout")
    for _ in range(2):
        t.converse(
            modelId=MODEL,
            messages=[{"role": "user", "content": [{"text": "Count: one"}]}],
            inferenceConfig={"maxTokens": 5},
        )
    assert len(t.spans) == 2
    summary = t.run_summary()
    assert summary["total_input_tokens"] > 0
    # Both spans counted
    model_breakdown = summary["model_breakdown"]
    assert MODEL in model_breakdown
    assert model_breakdown[MODEL]["invocations"] == 2


# ── run_context() helper ──────────────────────────────────────────────────────

def test_run_context_yields_tracer_and_flushes():
    flushed = []
    with run_context(user_id="live-ctx-u", tenant_id="live-ctx-t",
                     region=REGION, emit_backend="stdout") as t:
        resp = t.converse(
            modelId=MODEL,
            messages=[{"role": "user", "content": [{"text": "Say yes"}]}],
            inferenceConfig={"maxTokens": 5},
        )
        assert resp["ResponseMetadata"]["HTTPStatusCode"] == 200
    # After context exit the tracer has flushed — spans still readable
    assert len(t.spans) >= 1


def test_run_context_trace_id_is_stable():
    with run_context(user_id="live-id-u", tenant_id="live-id-t",
                     region=REGION, emit_backend="stdout") as t:
        tid_before = t.trace_id
        t.converse(
            modelId=MODEL,
            messages=[{"role": "user", "content": [{"text": "Hi"}]}],
            inferenceConfig={"maxTokens": 5},
        )
        assert t.trace_id == tid_before


# ── run_summary() shape ───────────────────────────────────────────────────────

def test_run_summary_contains_required_keys():
    t = RunTracer(user_id="live-summ-u", tenant_id="live-summ-t",
                  region=REGION, emit_backend="stdout")
    t.converse(
        modelId=MODEL,
        messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        inferenceConfig={"maxTokens": 5},
    )
    summary = t.run_summary()
    for key in ("total_estimated_cost_usd", "total_input_tokens", "total_output_tokens",
                "model_breakdown", "total_spans"):
        assert key in summary, f"Missing key: {key}"


# ── Explicit tool span ────────────────────────────────────────────────────────

def test_tool_span_recorded():
    t = RunTracer(user_id="live-tool-u", tenant_id="live-tool-t",
                  region=REGION, emit_backend="stdout")
    with t.span(SpanType.TOOL, tool_name="live_test_tool") as s:
        s.tool_output_tokens = 42
    assert len(t.spans) == 1
    span = t.spans[0]
    assert span.span_type == SpanType.TOOL.value
    assert span.tool_name == "live_test_tool"
