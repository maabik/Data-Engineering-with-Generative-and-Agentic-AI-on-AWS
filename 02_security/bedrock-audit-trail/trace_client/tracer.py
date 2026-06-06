"""BedrockTracer: injects consistent trace_id / metadata into every Bedrock call.

Usage — direct boto3 wrapping:

    from trace_client import BedrockTracer

    tracer = BedrockTracer(user_id="u123", tenant_id="acme", session_id="sess-abc")

    # Wraps bedrock-runtime.converse()
    response = tracer.converse(
        modelId="anthropic.claude-3-sonnet-20240229-v1:0",
        messages=[{"role": "user", "content": [{"type": "text", "text": "Hello"}]}],
    )

Usage — Strands agent:

    from trace_client import traced_agent
    from strands import Agent

    agent = Agent(model="anthropic.claude-3-sonnet-20240229-v1:0")
    result = traced_agent(agent, "What is 2+2?", user_id="u123", tenant_id="acme")

The trace_id is a UUID4 generated once per BedrockTracer instance.
Pass an explicit trace_id to link calls across service boundaries.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Optional

import boto3


class BedrockTracer:
    """Wraps boto3 bedrock-runtime to inject requestMetadata on every call."""

    def __init__(
        self,
        *,
        user_id: str = "",
        tenant_id: str = "",
        session_id: str = "",
        trace_id: Optional[str] = None,
        region: Optional[str] = None,
    ):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.session_id = session_id
        self.trace_id = trace_id or str(uuid.uuid4())
        self._client = boto3.client("bedrock-runtime", region_name=region)
        self._agent_client = boto3.client("bedrock-agent-runtime", region_name=region)

    @property
    def metadata(self) -> dict[str, str]:
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
        }

    def converse(self, **kwargs) -> dict:
        """bedrock-runtime.converse() with trace metadata injected."""
        kwargs.setdefault("additionalModelRequestFields", {})
        kwargs["requestMetadata"] = {**self.metadata, **kwargs.get("requestMetadata", {})}
        return self._client.converse(**kwargs)

    def converse_stream(self, **kwargs):
        """bedrock-runtime.converse_stream() with trace metadata injected."""
        kwargs["requestMetadata"] = {**self.metadata, **kwargs.get("requestMetadata", {})}
        return self._client.converse_stream(**kwargs)

    def invoke_model(self, **kwargs) -> dict:
        """bedrock-runtime.invoke_model() with trace metadata injected."""
        kwargs["requestMetadata"] = {**self.metadata, **kwargs.get("requestMetadata", {})}
        return self._client.invoke_model(**kwargs)

    def invoke_agent(self, **kwargs) -> dict:
        """bedrock-agent-runtime.invoke_agent() with session metadata injected.

        Bedrock Agent does not have a requestMetadata field, so we inject
        the trace context into sessionState.sessionAttributes.
        """
        session_state = kwargs.setdefault("sessionState", {})
        attrs = session_state.setdefault("sessionAttributes", {})
        attrs.update({k: str(v) for k, v in self.metadata.items()})
        return self._agent_client.invoke_agent(**kwargs)

    def child(self, **overrides) -> "BedrockTracer":
        """Create a child tracer that inherits the same trace_id but overrides other fields."""
        return BedrockTracer(
            user_id=overrides.get("user_id", self.user_id),
            tenant_id=overrides.get("tenant_id", self.tenant_id),
            session_id=overrides.get("session_id", self.session_id),
            trace_id=self.trace_id,  # propagate same trace_id
        )


def traced_invoke(
    fn: Callable,
    *args,
    user_id: str = "",
    tenant_id: str = "",
    session_id: str = "",
    trace_id: Optional[str] = None,
    **kwargs,
) -> Any:
    """Wrap any callable that accepts a `tracer` kwarg."""
    tracer = BedrockTracer(
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=session_id,
        trace_id=trace_id,
    )
    return fn(*args, tracer=tracer, **kwargs)


def traced_agent(
    agent,
    prompt: str,
    *,
    user_id: str = "",
    tenant_id: str = "",
    session_id: str = "",
    trace_id: Optional[str] = None,
) -> Any:
    """Invoke a Strands Agent with trace metadata injected into the request.

    Strands Agent doesn't expose requestMetadata directly. We monkey-patch
    the underlying boto3 client temporarily so all calls within the agent
    run carry the trace headers.
    """
    trace_id = trace_id or str(uuid.uuid4())
    metadata = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "trace_id": trace_id,
    }

    # Try Strands integration first
    try:
        from strands.telemetry import set_request_metadata  # type: ignore
        set_request_metadata(metadata)
        return agent(prompt)
    except ImportError:
        pass

    # Fallback: patch boto3 converse on the underlying model client
    try:
        model_client = agent.model  # strands.models.bedrock.BedrockModel
        original_converse = model_client._client.converse

        def patched_converse(**kwargs):
            kwargs["requestMetadata"] = {**metadata, **kwargs.get("requestMetadata", {})}
            return original_converse(**kwargs)

        model_client._client.converse = patched_converse
        try:
            return agent(prompt)
        finally:
            model_client._client.converse = original_converse
    except AttributeError:
        # Can't patch — run without metadata injection
        return agent(prompt)


@contextmanager
def trace_context(
    user_id: str = "",
    tenant_id: str = "",
    session_id: str = "",
    trace_id: Optional[str] = None,
):
    """Context manager that yields a BedrockTracer for a block of code."""
    tracer = BedrockTracer(
        user_id=user_id,
        tenant_id=tenant_id,
        session_id=session_id,
        trace_id=trace_id,
    )
    yield tracer
