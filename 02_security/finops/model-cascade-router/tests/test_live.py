"""Live integration tests for model-cascade-router.

Requires: AWS credentials with bedrock-runtime access in us-west-2.
Calls are routed to cheap inference profiles only (Nova Micro / Haiku)
to minimize cost during testing.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import boto3

from router.config import RouterConfig, TierConfig
from router.cascade import CascadeRouter, RouteDecision
from router.classifier import TaskClassifier, TaskComplexity

REGION = "us-west-2"

# Use cross-region inference profiles — required for on-demand throughput
_NANO_PROFILE  = "us.amazon.nova-micro-v1:0"
_MICRO_PROFILE = "us.amazon.nova-lite-v1:0"
_HAIKU_PROFILE = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# Cheap-only config: nano→micro→haiku ceiling so tests never touch expensive models
_CHEAP_TIERS = [
    TierConfig("nano",  _NANO_PROFILE,  max_tokens=50,  input_cost_per_1k=0.000035, output_cost_per_1k=0.00014),
    TierConfig("micro", _MICRO_PROFILE, max_tokens=50,  input_cost_per_1k=0.00006,  output_cost_per_1k=0.00024),
    TierConfig("small", _HAIKU_PROFILE, max_tokens=50,  input_cost_per_1k=0.001,    output_cost_per_1k=0.005),
]
_CHEAP_CONFIG = RouterConfig(tiers=_CHEAP_TIERS, max_tier_id="small")


def _router() -> CascadeRouter:
    return CascadeRouter(config=_CHEAP_CONFIG, region=REGION)


# ── Classifier: heuristic path ────────────────────────────────────────────────

def test_heuristic_trivial_for_simple_prompt():
    clf = TaskClassifier()
    result = clf.classify(
        [{"role": "user", "content": [{"text": "What is 2 + 2?"}]}]
    )
    assert result.complexity in (TaskComplexity.TRIVIAL, TaskComplexity.SIMPLE)
    assert result.strategy == "heuristic"


def test_heuristic_complex_for_long_prompt():
    clf = TaskClassifier()
    # >800 tokens triggers COMPLEX in the heuristic (threshold: tokens > 800)
    long_prompt = (
        "Explain the trade-offs between different distributed database consistency models, "
        "including linearizability, sequential consistency, causal consistency, and eventual "
        "consistency. For each model discuss performance implications, failure scenarios, "
        "and suitable use cases in production systems. " * 10
    )
    result = clf.classify(
        [{"role": "user", "content": [{"text": long_prompt}]}]
    )
    assert result.complexity in (TaskComplexity.MODERATE, TaskComplexity.COMPLEX, TaskComplexity.EXPERT)


# ── Router: basic route ───────────────────────────────────────────────────────

def test_route_simple_prompt_returns_response():
    router = _router()
    decision = router.route(
        messages=[{"role": "user", "content": [{"text": "What is 2 + 2?"}]}],
        inference_config={"maxTokens": 20},
    )
    assert decision.success
    assert decision.response is not None
    text = decision.response["output"]["message"]["content"][0]["text"]
    assert len(text) > 0


def test_route_selects_cheap_tier_for_trivial():
    router = _router()
    decision = router.route(
        messages=[{"role": "user", "content": [{"text": "What is 1 + 1?"}]}],
        inference_config={"maxTokens": 10},
    )
    assert decision.success
    # Trivial / simple prompts should land on nano or micro — never haiku
    assert decision.tier.tier_id in ("nano", "micro")


def test_route_records_tokens_and_cost():
    router = _router()
    decision = router.route(
        messages=[{"role": "user", "content": [{"text": "What is the capital of France?"}]}],
        inference_config={"maxTokens": 20},
    )
    assert decision.success
    assert decision.input_tokens > 0
    assert decision.output_tokens > 0
    assert decision.estimated_cost_usd > 0.0


def test_route_latency_ms_is_positive():
    router = _router()
    decision = router.route(
        messages=[{"role": "user", "content": [{"text": "Hello"}]}],
        inference_config={"maxTokens": 5},
    )
    assert decision.latency_ms > 0


# ── Router: floor / ceiling enforcement ──────────────────────────────────────

def test_floor_enforcement_bumps_nano_to_micro():
    config = RouterConfig(tiers=_CHEAP_TIERS, min_tier_id="micro", max_tier_id="small")
    router = CascadeRouter(config=config, region=REGION)
    decision = router.route(
        messages=[{"role": "user", "content": [{"text": "Say OK"}]}],
        inference_config={"maxTokens": 10},
    )
    assert decision.success
    # Even for trivial prompts, floor forces at least "micro"
    assert decision.tier.tier_id in ("micro", "small")


def test_ceiling_prevents_escalation_beyond_micro():
    config = RouterConfig(tiers=_CHEAP_TIERS, max_tier_id="micro")
    router = CascadeRouter(config=config, region=REGION)
    decision = router.route(
        messages=[{"role": "user", "content": [{"text": "Say yes"}]}],
        inference_config={"maxTokens": 10},
    )
    assert decision.success
    assert decision.tier.tier_id in ("nano", "micro")


# ── Router: task_label override ───────────────────────────────────────────────

def test_task_label_override_pins_tier():
    config = RouterConfig(
        tiers=_CHEAP_TIERS,
        task_overrides={"summarize": "micro"},
        max_tier_id="small",
    )
    router = CascadeRouter(config=config, region=REGION)
    decision = router.route(
        messages=[{"role": "user", "content": [{"text": "Summarise this."}]}],
        task_label="summarize",
        inference_config={"maxTokens": 20},
    )
    assert decision.success
    # Override pins to micro; nano is below the override floor
    assert decision.tier.tier_id in ("micro", "small")


# ── Router: classify() dry-run ────────────────────────────────────────────────

def test_classify_returns_classifier_result():
    router = _router()
    result = router.classify(
        [{"role": "user", "content": [{"text": "What color is the sky?"}]}]
    )
    assert result.complexity is not None
    assert 0.0 <= result.confidence <= 1.0
    assert result.strategy in ("heuristic", "llm")


# ── RouteDecision shape ───────────────────────────────────────────────────────

def test_route_decision_has_classifier():
    router = _router()
    decision = router.route(
        messages=[{"role": "user", "content": [{"text": "Define photosynthesis"}]}],
        inference_config={"maxTokens": 30},
    )
    assert decision.classifier is not None
    assert decision.classifier.complexity is not None
    assert decision.escalation_path == []  # no escalation for straightforward call
