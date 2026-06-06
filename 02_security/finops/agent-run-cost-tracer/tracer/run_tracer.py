"""RunTracer: correlates every sub-call of one user turn under a single trace_id.

A "run" = one user-visible turn: the question a user asks and everything that
happens before the answer is returned.  A single run may trigger:
  - N model invocations  (orchestration + sub-agent calls)
  - M tool calls         (Lambda, code interpreter, web search …)
  - P retrieval queries  (Knowledge Base / vector search)
  - Q embedding calls    (chunk ingestion, query embedding)

RunTracer wraps each boto3 client operation and records a RunSpan for every
sub-call. Spans are emitted to the FinOps data lake via a Firehose PUT or
written to stdout/CloudWatch for the nightly stitcher to pick up.

Usage — context manager:

    from tracer import run_context

    with run_context(user_id="u1", tenant_id="acme", session_id="sess-1") as t:
        response = t.converse(modelId=..., messages=...)
        # tool calls, KB retrievals, etc. all inside the same context

Usage — Strands agent:

    from tracer import traced_strands_agent
    from strands import Agent

    agent = Agent(model="anthropic.claude-3-sonnet-20240229-v1:0")
    result = traced_strands_agent(
        agent, "Summarise last quarter's revenue",
        user_id="u1", tenant_id="acme",
    )

Usage — manual span recording:

    from tracer import RunTracer, SpanType

    t = RunTracer(user_id="u1", tenant_id="acme")
    with t.span(SpanType.MODEL, model_id="anthropic.claude-3-sonnet-...",
                input_tokens=100, output_tokens=50):
        ...  # the actual call
    t.flush()
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Generator, Optional

import boto3

logger = logging.getLogger(__name__)


class SpanType(str, Enum):
    MODEL = "model"
    TOOL = "tool"
    RETRIEVAL = "retrieval"
    EMBEDDING = "embedding"
    AGENT = "agent"          # top-level agent turn
    SUB_AGENT = "sub_agent"  # nested agent invocation


@dataclass
class RunSpan:
    span_id: str
    trace_id: str
    run_id: str
    span_type: str          # SpanType value
    started_at: str         # ISO UTC
    ended_at: str           # ISO UTC
    duration_ms: int
    # model spans
    model_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    # tool spans
    tool_name: str = ""
    tool_success: bool = True
    # retrieval spans
    kb_id: str = ""
    retrieval_query: str = ""
    retrieved_chunks: int = 0
    # embedding spans
    embedding_model: str = ""
    embedding_input_tokens: int = 0
    # agent metadata
    agent_id: str = ""
    agent_name: str = ""
    # attribution
    user_id: str = ""
    tenant_id: str = ""
    session_id: str = ""
    workload: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# Pricing — same table as ai-finops-data-lake for consistency
_DEFAULT_PRICING: dict[str, tuple[float, float]] = {
    "anthropic.claude-3-5-sonnet": (0.003, 0.015),
    "anthropic.claude-3-5-haiku":  (0.0008, 0.004),
    "anthropic.claude-3-sonnet":   (0.003, 0.015),
    "anthropic.claude-3-haiku":    (0.00025, 0.00125),
    "anthropic.claude-3-opus":     (0.015, 0.075),
    "amazon.nova-pro":             (0.0008, 0.0032),
    "amazon.nova-lite":            (0.00006, 0.00024),
    "amazon.nova-micro":           (0.000035, 0.00014),
    "amazon.titan-text-express":   (0.0002, 0.0006),
    "amazon.titan-embed":          (0.00002, 0.0),
    "amazon.titan-text-lite":      (0.00015, 0.0002),
    "meta.llama3":                 (0.00065, 0.00065),
    "cohere.embed":                (0.0001, 0.0),
    "cohere.command-r-plus":       (0.003, 0.015),
    "cohere.command-r":            (0.0005, 0.0015),
}


def _price(model_id: str) -> tuple[float, float]:
    import re
    ml = re.sub(r"^(us|eu|ap|global)\.", "", model_id.lower())
    best = ""
    for k in _DEFAULT_PRICING:
        if ml.startswith(k.lower()) and len(k) > len(best):
            best = k
    return _DEFAULT_PRICING.get(best, (0.0, 0.0))


def _estimate(input_t: int, output_t: int, model_id: str) -> float:
    inp, out = _price(model_id)
    return round((input_t / 1000.0) * inp + (output_t / 1000.0) * out, 8)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunTracer:
    """Records all sub-calls within a single agent run and emits span events.

    Span emission backends (configurable via emit_backend param):
      "firehose"  — PUT to a Kinesis Firehose stream (default when FINOPS_FIREHOSE_STREAM is set)
      "cloudwatch" — PUT to a CloudWatch log group (FINOPS_CW_LOG_GROUP)
      "stdout"    — JSON lines to stdout (default in local/test mode)
      "none"      — discard (useful in unit tests)
    """

    def __init__(
        self,
        *,
        user_id: str = "",
        tenant_id: str = "",
        session_id: str = "",
        workload: str = "",
        trace_id: Optional[str] = None,
        run_id: Optional[str] = None,
        emit_backend: Optional[str] = None,
        firehose_stream: Optional[str] = None,
        cw_log_group: Optional[str] = None,
        pricing_overrides: Optional[dict] = None,
        region: Optional[str] = None,
    ):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.workload = workload
        self.trace_id = trace_id or str(uuid.uuid4())
        self.run_id = run_id or str(uuid.uuid4())
        self._spans: list[RunSpan] = []
        self._pricing = {**_DEFAULT_PRICING, **(pricing_overrides or {})}
        self._region = region

        # Resolve emission backend
        if emit_backend is None:
            if firehose_stream or os.environ.get("FINOPS_FIREHOSE_STREAM"):
                emit_backend = "firehose"
            elif cw_log_group or os.environ.get("FINOPS_CW_LOG_GROUP"):
                emit_backend = "cloudwatch"
            elif os.environ.get("FINOPS_EMIT_BACKEND"):
                emit_backend = os.environ["FINOPS_EMIT_BACKEND"]
            else:
                emit_backend = "stdout"

        self._backend = emit_backend
        self._firehose_stream = firehose_stream or os.environ.get("FINOPS_FIREHOSE_STREAM", "")
        self._cw_log_group = cw_log_group or os.environ.get("FINOPS_CW_LOG_GROUP", "")
        self._boto_session = boto3.Session(region_name=region)

    # ── Span recording ────────────────────────────────────────────────────────

    @contextmanager
    def span(
        self,
        span_type: SpanType,
        **kwargs,
    ) -> Generator[RunSpan, None, None]:
        """Context manager that records a span around a block of code."""
        started = time.monotonic()
        started_at = _now_iso()
        s = RunSpan(
            span_id=str(uuid.uuid4()),
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_type=span_type.value,
            started_at=started_at,
            ended_at="",
            duration_ms=0,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            session_id=self.session_id,
            workload=self.workload,
            **{k: v for k, v in kwargs.items() if k in RunSpan.__dataclass_fields__},
        )
        try:
            yield s
        except Exception as exc:
            s.error = str(exc)
            raise
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            s.ended_at = _now_iso()
            s.duration_ms = elapsed_ms
            s.total_tokens = s.input_tokens + s.output_tokens
            if s.span_type == SpanType.MODEL.value and s.model_id:
                s.estimated_cost_usd = _estimate(s.input_tokens, s.output_tokens, s.model_id)
            elif s.span_type == SpanType.EMBEDDING.value and s.embedding_model:
                s.estimated_cost_usd = _estimate(s.embedding_input_tokens, 0, s.embedding_model)
            self._spans.append(s)
            self._emit_one(s)

    def record_model_span(
        self,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: int,
        error: str = "",
    ) -> RunSpan:
        """Record a model invocation span from known token counts."""
        now = _now_iso()
        s = RunSpan(
            span_id=str(uuid.uuid4()),
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_type=SpanType.MODEL.value,
            started_at=now,
            ended_at=now,
            duration_ms=duration_ms,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            estimated_cost_usd=_estimate(input_tokens, output_tokens, model_id),
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            session_id=self.session_id,
            workload=self.workload,
            error=error,
        )
        self._spans.append(s)
        self._emit_one(s)
        return s

    def record_tool_span(
        self,
        tool_name: str,
        duration_ms: int,
        success: bool = True,
        error: str = "",
    ) -> RunSpan:
        now = _now_iso()
        s = RunSpan(
            span_id=str(uuid.uuid4()),
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_type=SpanType.TOOL.value,
            started_at=now,
            ended_at=now,
            duration_ms=duration_ms,
            tool_name=tool_name,
            tool_success=success,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            session_id=self.session_id,
            workload=self.workload,
            error=error,
        )
        self._spans.append(s)
        self._emit_one(s)
        return s

    def record_retrieval_span(
        self,
        kb_id: str,
        query: str,
        retrieved_chunks: int,
        duration_ms: int,
        error: str = "",
    ) -> RunSpan:
        now = _now_iso()
        s = RunSpan(
            span_id=str(uuid.uuid4()),
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_type=SpanType.RETRIEVAL.value,
            started_at=now,
            ended_at=now,
            duration_ms=duration_ms,
            kb_id=kb_id,
            retrieval_query=query[:500],
            retrieved_chunks=retrieved_chunks,
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            session_id=self.session_id,
            workload=self.workload,
            error=error,
        )
        self._spans.append(s)
        self._emit_one(s)
        return s

    def record_embedding_span(
        self,
        embedding_model: str,
        input_tokens: int,
        duration_ms: int,
        error: str = "",
    ) -> RunSpan:
        now = _now_iso()
        s = RunSpan(
            span_id=str(uuid.uuid4()),
            trace_id=self.trace_id,
            run_id=self.run_id,
            span_type=SpanType.EMBEDDING.value,
            started_at=now,
            ended_at=now,
            duration_ms=duration_ms,
            embedding_model=embedding_model,
            embedding_input_tokens=input_tokens,
            estimated_cost_usd=_estimate(input_tokens, 0, embedding_model),
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            session_id=self.session_id,
            workload=self.workload,
            error=error,
        )
        self._spans.append(s)
        self._emit_one(s)
        return s

    # ── boto3 wrappers ────────────────────────────────────────────────────────

    def converse(self, **kwargs) -> dict:
        """bedrock-runtime.converse() with span recording."""
        model_id = kwargs.get("modelId", "")
        kwargs["requestMetadata"] = {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            **kwargs.get("requestMetadata", {}),
        }
        client = self._boto_session.client("bedrock-runtime")
        with self.span(SpanType.MODEL, model_id=model_id) as s:
            resp = client.converse(**kwargs)
            usage = resp.get("usage", {})
            s.input_tokens = usage.get("inputTokens", 0)
            s.output_tokens = usage.get("outputTokens", 0)
        return resp

    def retrieve(self, knowledge_base_id: str, query: str, **kwargs) -> dict:
        """bedrock-agent-runtime.retrieve() with span recording."""
        client = self._boto_session.client("bedrock-agent-runtime")
        with self.span(SpanType.RETRIEVAL, kb_id=knowledge_base_id,
                       retrieval_query=query[:500]) as s:
            resp = client.retrieve(
                knowledgeBaseId=knowledge_base_id,
                retrievalQuery={"text": query},
                **kwargs,
            )
            s.retrieved_chunks = len(resp.get("retrievalResults", []))
        return resp

    def embed(self, model_id: str, text: str, **kwargs) -> dict:
        """bedrock-runtime.invoke_model() for embedding with span recording."""
        client = self._boto_session.client("bedrock-runtime")
        body = json.dumps({"inputText": text, **kwargs})
        with self.span(SpanType.EMBEDDING, embedding_model=model_id) as s:
            resp = client.invoke_model(
                modelId=model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            # Approximate: split on whitespace
            s.embedding_input_tokens = len(text.split())
            s.embedding_input_tokens = max(s.embedding_input_tokens, 1)
        return resp

    # ── Properties / summary ──────────────────────────────────────────────────

    @property
    def spans(self) -> list[RunSpan]:
        return list(self._spans)

    def run_summary(self) -> dict:
        """Aggregate all spans into a single run-level cost summary."""
        model_spans = [s for s in self._spans if s.span_type == SpanType.MODEL.value]
        tool_spans = [s for s in self._spans if s.span_type == SpanType.TOOL.value]
        retrieval_spans = [s for s in self._spans if s.span_type == SpanType.RETRIEVAL.value]
        embedding_spans = [s for s in self._spans if s.span_type == SpanType.EMBEDDING.value]

        total_cost = sum(s.estimated_cost_usd for s in self._spans)
        total_input_tokens = sum(s.input_tokens for s in model_spans)
        total_output_tokens = sum(s.output_tokens for s in model_spans)
        total_embed_tokens = sum(s.embedding_input_tokens for s in embedding_spans)

        # Model breakdown: cost per model_id
        model_costs: dict[str, dict] = {}
        for s in model_spans:
            if s.model_id not in model_costs:
                model_costs[s.model_id] = {"invocations": 0, "input_tokens": 0,
                                            "output_tokens": 0, "cost_usd": 0.0}
            model_costs[s.model_id]["invocations"] += 1
            model_costs[s.model_id]["input_tokens"] += s.input_tokens
            model_costs[s.model_id]["output_tokens"] += s.output_tokens
            model_costs[s.model_id]["cost_usd"] += s.estimated_cost_usd

        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "workload": self.workload,
            "total_spans": len(self._spans),
            "model_invocations": len(model_spans),
            "tool_invocations": len(tool_spans),
            "retrieval_calls": len(retrieval_spans),
            "embedding_calls": len(embedding_spans),
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_input_tokens + total_output_tokens,
            "total_embedding_tokens": total_embed_tokens,
            "total_estimated_cost_usd": round(total_cost, 8),
            "model_breakdown": model_costs,
            "total_retrieved_chunks": sum(s.retrieved_chunks for s in retrieval_spans),
            "tool_errors": sum(1 for s in tool_spans if not s.tool_success),
            "span_errors": sum(1 for s in self._spans if s.error),
        }

    # ── Emission ──────────────────────────────────────────────────────────────

    def _emit_one(self, span: RunSpan):
        if self._backend == "none":
            return
        line = json.dumps(span.to_dict(), default=str)
        if self._backend == "stdout":
            print(line)
        elif self._backend == "firehose":
            self._emit_firehose(line)
        elif self._backend == "cloudwatch":
            self._emit_cloudwatch(line)

    def _emit_firehose(self, line: str):
        try:
            client = self._boto_session.client("firehose")
            client.put_record(
                DeliveryStreamName=self._firehose_stream,
                Record={"Data": (line + "\n").encode()},
            )
        except Exception as e:
            logger.warning("Firehose emit failed: %s", e)

    def _emit_cloudwatch(self, line: str):
        stream_name = f"agent-run-cost/{self.run_id[:8]}"
        try:
            client = self._boto_session.client("logs")
            if not getattr(self, "_cw_stream_created", False):
                try:
                    client.create_log_stream(
                        logGroupName=self._cw_log_group,
                        logStreamName=stream_name,
                    )
                except client.exceptions.ResourceAlreadyExistsException:
                    pass
                self._cw_stream_created = True
            client.put_log_events(
                logGroupName=self._cw_log_group,
                logStreamName=stream_name,
                logEvents=[{
                    "timestamp": int(time.time() * 1000),
                    "message": line,
                }],
            )
        except Exception as e:
            logger.warning("CloudWatch emit failed: %s", e)

    def flush(self):
        """Emit a run-summary span (span_type=agent) summarising the whole run."""
        summary = self.run_summary()
        line = json.dumps({"event_type": "RUN_SUMMARY", **summary}, default=str)
        if self._backend == "stdout":
            print(line)
        elif self._backend == "firehose" and self._firehose_stream:
            self._emit_firehose(line)
        elif self._backend == "cloudwatch" and self._cw_log_group:
            self._emit_cloudwatch(line)


# ── Context manager helper ────────────────────────────────────────────────────

@contextmanager
def run_context(
    user_id: str = "",
    tenant_id: str = "",
    session_id: str = "",
    workload: str = "",
    trace_id: Optional[str] = None,
    emit_backend: Optional[str] = None,
    **kwargs,
) -> Generator[RunTracer, None, None]:
    """Yield a RunTracer for a block; flush on exit."""
    tracer = RunTracer(
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=session_id,
        workload=workload,
        trace_id=trace_id,
        emit_backend=emit_backend,
        **kwargs,
    )
    try:
        yield tracer
    finally:
        tracer.flush()


# ── Strands integration ───────────────────────────────────────────────────────

def traced_strands_agent(
    agent,
    prompt: str,
    *,
    user_id: str = "",
    tenant_id: str = "",
    session_id: str = "",
    workload: str = "",
    trace_id: Optional[str] = None,
    emit_backend: Optional[str] = None,
    **tracer_kwargs,
) -> Any:
    """Run a Strands agent with RunTracer span collection.

    Patches the agent's underlying boto3 converse call to intercept token
    counts after each model invocation. Retrieval and embedding calls made
    through the standard KB/embedding tool names are intercepted via a
    tool-call observer callback (Strands callback_handler).
    """
    tracer = RunTracer(
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=session_id,
        workload=workload,
        trace_id=trace_id,
        emit_backend=emit_backend or "none",
        **tracer_kwargs,
    )

    # Top-level agent span
    started = time.monotonic()

    def _callback(event: dict):
        """Strands callback_handler events."""
        etype = event.get("event")
        if etype == "model_response":
            usage = event.get("usage", {})
            tracer.record_model_span(
                model_id=event.get("model_id", ""),
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
                duration_ms=event.get("latency_ms", 0),
            )
        elif etype == "tool_call":
            tool_name = event.get("name", "")
            # Classify retrieval and embedding tools
            tool_lower = tool_name.lower()
            if any(kw in tool_lower for kw in ("retrieve", "search", "knowledge", "kb")):
                tracer.record_retrieval_span(
                    kb_id=event.get("input", {}).get("knowledgeBaseId", ""),
                    query=str(event.get("input", {}).get("query", ""))[:500],
                    retrieved_chunks=event.get("output_count", 0),
                    duration_ms=event.get("latency_ms", 0),
                    error=event.get("error", ""),
                )
            elif any(kw in tool_lower for kw in ("embed", "embedding")):
                tracer.record_embedding_span(
                    embedding_model=event.get("model_id", ""),
                    input_tokens=event.get("input_tokens", 0),
                    duration_ms=event.get("latency_ms", 0),
                )
            else:
                tracer.record_tool_span(
                    tool_name=tool_name,
                    duration_ms=event.get("latency_ms", 0),
                    success=not event.get("error"),
                    error=event.get("error", ""),
                )

    # Inject requestMetadata into all Bedrock calls via model client patch
    metadata = {
        "trace_id": tracer.trace_id,
        "run_id": tracer.run_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
    }

    try:
        model_client = agent.model
        original_converse = model_client._client.converse

        def patched_converse(**kwargs):
            kwargs["requestMetadata"] = {**metadata, **kwargs.get("requestMetadata", {})}
            return original_converse(**kwargs)

        model_client._client.converse = patched_converse
    except AttributeError:
        pass  # Not a Strands-style agent with .model._client

    try:
        result = agent(prompt, callback_handler=_callback)
    except TypeError:
        # Agent doesn't accept callback_handler
        result = agent(prompt)
    finally:
        try:
            model_client._client.converse = original_converse
        except Exception:
            pass
        tracer.flush()

    return result, tracer
