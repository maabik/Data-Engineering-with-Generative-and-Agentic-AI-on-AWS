"""Tests for router/cascade.py"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, call, patch
from router.cascade import CascadeRouter, RouteDecision, _is_refusal, _estimate_cost
from router.config import RouterConfig, TierConfig, DEFAULT_TIERS
from router.classifier import TaskComplexity


def _mock_converse_response(text="ok", input_t=100, output_t=50, stop="end_turn"):
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "usage": {"inputTokens": input_t, "outputTokens": output_t},
        "stopReason": stop,
    }


def _mock_client(response=None, side_effect=None):
    client = MagicMock()
    if side_effect:
        client.converse.side_effect = side_effect
    else:
        client.converse.return_value = response or _mock_converse_response()
    # Provide a realistic exceptions attribute
    client.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})
    return client


# ── _is_refusal ────────────────────────────────────────────────────────────────

class TestIsRefusal:
    def test_content_filtered(self):
        assert _is_refusal({"stopReason": "content_filtered"})

    def test_guardrail_intervened(self):
        assert _is_refusal({"stopReason": "guardrail_intervened"})

    def test_normal_response(self):
        assert not _is_refusal({"stopReason": "end_turn",
                                 "usage": {"outputTokens": 50}})

    def test_empty_output_tokens(self):
        assert _is_refusal({"stopReason": "end_turn",
                             "usage": {"outputTokens": 0}})

    def test_end_turn_with_output(self):
        assert not _is_refusal({"stopReason": "end_turn",
                                 "usage": {"outputTokens": 100}})


# ── _estimate_cost ─────────────────────────────────────────────────────────────

class TestEstimateCost:
    def test_zero_tokens_zero_cost(self):
        t = TierConfig("x", "m", input_cost_per_1k=0.001, output_cost_per_1k=0.005)
        assert _estimate_cost(t, 0, 0) == 0.0

    def test_cost_formula(self):
        t = TierConfig("x", "m", input_cost_per_1k=0.003, output_cost_per_1k=0.015)
        cost = _estimate_cost(t, 1000, 500)
        expected = 1.0 * 0.003 + 0.5 * 0.015
        assert abs(cost - expected) < 1e-9


# ── CascadeRouter routing ──────────────────────────────────────────────────────

class TestCascadeRouterBasic:
    def _router(self, client=None, **cfg_kw):
        client = client or _mock_client()
        cfg = RouterConfig(**cfg_kw)
        return CascadeRouter(config=cfg, bedrock_client=client)

    def test_route_returns_decision(self):
        r = self._router()
        d = r.route([{"role": "user", "content": [{"text": "What is 2+2?"}]}])
        assert isinstance(d, RouteDecision)
        assert d.success

    def test_trivial_routes_to_cheap_tier(self):
        client = _mock_client()
        router = self._router(client=client)
        d = router.route([{"role": "user", "content": [{"text": "What is 2+2?"}]}])
        # Trivial → nano or micro
        assert d.tier.tier_id in ("nano", "micro", "small")

    def test_expert_routes_to_flagship(self):
        client = _mock_client()
        router = self._router(client=client)
        d = router.route([{"role": "user", "content": [{"text": "Perform a formal proof of this theorem."}]}])
        assert d.tier.tier_id == "flagship"

    def test_response_attached_to_decision(self):
        resp = _mock_converse_response("The answer is 4.")
        client = _mock_client(response=resp)
        d = self._router(client=client).route(
            [{"role": "user", "content": [{"text": "What is 2+2?"}]}]
        )
        assert d.response["output"]["message"]["content"][0]["text"] == "The answer is 4."

    def test_tokens_recorded(self):
        resp = _mock_converse_response(input_t=200, output_t=80)
        client = _mock_client(response=resp)
        d = self._router(client=client).route(
            [{"role": "user", "content": [{"text": "Hello"}]}]
        )
        assert d.input_tokens == 200
        assert d.output_tokens == 80

    def test_cost_positive_for_model_tier(self):
        client = _mock_client(_mock_converse_response(input_t=1000, output_t=500))
        d = self._router(client=client).route(
            [{"role": "user", "content": [{"text": "Hello"}]}]
        )
        assert d.estimated_cost_usd >= 0

    def test_system_prompt_forwarded(self):
        client = _mock_client()
        router = self._router(client=client)
        router.route(
            [{"role": "user", "content": [{"text": "Hello"}]}],
            system="You are a helpful assistant.",
        )
        kwargs = client.converse.call_args[1]
        assert "system" in kwargs
        assert kwargs["system"][0]["text"] == "You are a helpful assistant."

    def test_request_metadata_forwarded(self):
        client = _mock_client()
        router = self._router(client=client)
        router.route(
            [{"role": "user", "content": [{"text": "Hello"}]}],
            request_metadata={"trace_id": "abc"},
        )
        kwargs = client.converse.call_args[1]
        assert kwargs["requestMetadata"]["trace_id"] == "abc"


# ── Floor / ceiling enforcement ────────────────────────────────────────────────

class TestFloorCeiling:
    def test_floor_prevents_cheap_tier(self):
        client = _mock_client()
        cfg = RouterConfig(min_tier_id="large")
        router = CascadeRouter(config=cfg, bedrock_client=client)
        d = router.route([{"role": "user", "content": [{"text": "What is 2+2?"}]}])
        idx = cfg.tier_index(d.tier.tier_id)
        floor_idx = cfg.floor_index()
        assert idx >= floor_idx

    def test_ceiling_prevents_expensive_tier(self):
        client = _mock_client()
        cfg = RouterConfig(max_tier_id="small")
        router = CascadeRouter(config=cfg, bedrock_client=client)
        d = router.route([{"role": "user", "content": [{"text": "Prove Fermat's Last Theorem."}]}])
        idx = cfg.tier_index(d.tier.tier_id)
        ceiling_idx = cfg.ceiling_index()
        assert idx <= ceiling_idx


# ── Task overrides ─────────────────────────────────────────────────────────────

class TestTaskOverrides:
    def test_task_override_floors_to_large(self):
        client = _mock_client()
        cfg = RouterConfig(task_overrides={"code_generation": "large"})
        router = CascadeRouter(config=cfg, bedrock_client=client)
        d = router.route(
            [{"role": "user", "content": [{"text": "What is 2+2?"}]}],
            task_label="code_generation",
        )
        idx = cfg.tier_index(d.tier.tier_id)
        assert idx >= cfg.tier_index("large")

    def test_unknown_task_label_ignored(self):
        client = _mock_client()
        cfg = RouterConfig(task_overrides={"code_generation": "large"})
        router = CascadeRouter(config=cfg, bedrock_client=client)
        d = router.route(
            [{"role": "user", "content": [{"text": "What is 2+2?"}]}],
            task_label="factual_qa",
        )
        assert d.success


# ── Escalation ────────────────────────────────────────────────────────────────

class TestEscalation:
    def test_escalates_on_refusal(self):
        refusal = _mock_converse_response(stop="content_filtered", output_t=0)
        ok = _mock_converse_response("ok answer", output_t=50)
        client = _mock_client()
        client.converse.side_effect = [refusal, ok]
        client.exceptions.ThrottlingException = type("ThrottlingException", (Exception,), {})

        cfg = RouterConfig(retry_on_refusal=True)
        router = CascadeRouter(config=cfg, bedrock_client=client)
        d = router.route([{"role": "user", "content": [{"text": "What is 2+2?"}]}])
        assert d.success
        assert len(d.escalation_path) == 1

    def test_escalates_on_throttle(self):
        ThrottlingException = type("ThrottlingException", (Exception,), {})
        client = MagicMock()
        client.converse.side_effect = [
            ThrottlingException("throttled"),
            _mock_converse_response("ok"),
        ]
        client.exceptions.ThrottlingException = ThrottlingException

        cfg = RouterConfig()
        router = CascadeRouter(config=cfg, bedrock_client=client)
        d = router.route([{"role": "user", "content": [{"text": "Hello"}]}])
        assert d.success
        assert len(d.escalation_path) >= 1

    def test_all_tiers_exhausted_returns_error(self):
        client = _mock_client()
        client.converse.return_value = _mock_converse_response(stop="content_filtered", output_t=0)

        cfg = RouterConfig(max_retries=2)
        router = CascadeRouter(config=cfg, bedrock_client=client)
        d = router.route([{"role": "user", "content": [{"text": "Hello"}]}])
        assert not d.success
        assert d.error is not None

    def test_no_retry_on_refusal_when_disabled(self):
        refusal = _mock_converse_response(stop="content_filtered", output_t=0)
        client = _mock_client(response=refusal)

        cfg = RouterConfig(retry_on_refusal=False)
        router = CascadeRouter(config=cfg, bedrock_client=client)
        d = router.route([{"role": "user", "content": [{"text": "Hello"}]}])
        # With retry disabled, refusal is accepted as a valid response
        assert d.success
        assert d.escalation_path == []


# ── classify dry-run ──────────────────────────────────────────────────────────

class TestClassifyDryRun:
    def test_classify_returns_result(self):
        router = CascadeRouter(bedrock_client=_mock_client())
        r = router.classify([{"role": "user", "content": [{"text": "What is 2+2?"}]}])
        assert r.complexity in list(TaskComplexity)
        assert 0.0 <= r.confidence <= 1.0
