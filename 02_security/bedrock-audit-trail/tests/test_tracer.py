"""Tests for trace_client/tracer.py (no live AWS)."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import uuid


class TestBedrockTracer:
    def test_trace_id_auto_generated(self):
        from trace_client import BedrockTracer
        t = BedrockTracer()
        assert t.trace_id
        uuid.UUID(t.trace_id)  # valid UUID

    def test_explicit_trace_id_preserved(self):
        from trace_client import BedrockTracer
        tid = "my-custom-trace-id"
        t = BedrockTracer(trace_id=tid)
        assert t.trace_id == tid

    def test_metadata_dict(self):
        from trace_client import BedrockTracer
        t = BedrockTracer(user_id="u1", tenant_id="acme", session_id="s1", trace_id="t1")
        m = t.metadata
        assert m["user_id"] == "u1"
        assert m["tenant_id"] == "acme"
        assert m["session_id"] == "s1"
        assert m["trace_id"] == "t1"

    def test_child_inherits_trace_id(self):
        from trace_client import BedrockTracer
        parent = BedrockTracer(user_id="u1", tenant_id="acme", trace_id="shared-trace")
        child = parent.child(user_id="u2")
        assert child.trace_id == "shared-trace"
        assert child.user_id == "u2"
        assert child.tenant_id == "acme"

    def test_converse_injects_metadata(self, monkeypatch):
        from trace_client import BedrockTracer
        captured = {}

        class FakeClient:
            def converse(self, **kwargs):
                captured.update(kwargs)
                return {"output": {}}

        t = BedrockTracer(user_id="u1", tenant_id="acme", trace_id="tr-1")
        monkeypatch.setattr(t, "_client", FakeClient())

        t.converse(modelId="anthropic.claude-3-sonnet-20240229-v1:0",
                   messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}])

        assert captured["requestMetadata"]["user_id"] == "u1"
        assert captured["requestMetadata"]["trace_id"] == "tr-1"

    def test_converse_merges_existing_metadata(self, monkeypatch):
        from trace_client import BedrockTracer
        captured = {}

        class FakeClient:
            def converse(self, **kwargs):
                captured.update(kwargs)
                return {}

        t = BedrockTracer(user_id="u1", trace_id="tr-1")
        monkeypatch.setattr(t, "_client", FakeClient())

        t.converse(
            modelId="anthropic.claude-3-sonnet-20240229-v1:0",
            messages=[],
            requestMetadata={"custom_field": "cv"},
        )
        # custom_field preserved; tracer fields also injected
        assert captured["requestMetadata"]["custom_field"] == "cv"
        assert captured["requestMetadata"]["trace_id"] == "tr-1"

    def test_invoke_agent_injects_session_attributes(self, monkeypatch):
        from trace_client import BedrockTracer
        captured = {}

        class FakeAgentClient:
            def invoke_agent(self, **kwargs):
                captured.update(kwargs)
                return {}

        t = BedrockTracer(user_id="u1", tenant_id="acme", trace_id="tr-1")
        monkeypatch.setattr(t, "_agent_client", FakeAgentClient())

        t.invoke_agent(agentId="ag-1", agentAliasId="al-1",
                       sessionId="sess-1", inputText="hello")
        attrs = captured["sessionState"]["sessionAttributes"]
        assert attrs["user_id"] == "u1"
        assert attrs["trace_id"] == "tr-1"

    def test_unique_trace_ids_per_instance(self):
        from trace_client import BedrockTracer
        ids = {BedrockTracer().trace_id for _ in range(10)}
        assert len(ids) == 10

    def test_trace_context_manager(self):
        from trace_client.tracer import trace_context
        with trace_context(user_id="u1", tenant_id="acme") as tracer:
            assert tracer.user_id == "u1"
            assert tracer.tenant_id == "acme"
            assert tracer.trace_id  # auto-generated


class TestTracedAgent:
    def test_traced_agent_no_strands_no_model_attr(self):
        """traced_agent gracefully falls back when agent has no .model attribute."""
        from trace_client import traced_agent

        class SimpleAgent:
            def __call__(self, prompt):
                return f"response to: {prompt}"

        agent = SimpleAgent()
        result = traced_agent(agent, "hello", user_id="u1")
        assert "hello" in result

    def test_traced_agent_patches_model_client(self, monkeypatch):
        from trace_client import traced_agent
        captured = {}

        class FakeModelClient:
            class _client:
                @staticmethod
                def converse(**kwargs):
                    captured.update(kwargs)
                    return {"output": {"message": {"content": [{"text": "ok"}]}}}

        class StrandsLikeAgent:
            model = FakeModelClient()

            def __call__(self, prompt):
                return self.model._client.converse(
                    modelId="anthropic.claude-3-sonnet",
                    messages=[{"role": "user", "content": prompt}],
                )

        agent = StrandsLikeAgent()

        # Make model._client.converse patchable by making it an instance method
        original = agent.model._client.converse
        patched_results = {}

        def patch_fn(**kwargs):
            patched_results.update(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(agent.model._client, "converse", patch_fn)

        traced_agent(agent, "test prompt", user_id="u1", trace_id="tr-x")
        # The patched converse was called
        assert patched_results.get("modelId") == "anthropic.claude-3-sonnet"
