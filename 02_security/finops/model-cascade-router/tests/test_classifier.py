"""Tests for router/classifier.py"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import pytest
from router.classifier import (
    TaskComplexity,
    ClassifierResult,
    classify_heuristic,
    classify_llm,
    TaskClassifier,
    COMPLEXITY_TO_TIER,
)


def _msgs(text):
    return [{"role": "user", "content": [{"text": text}]}]


# ── classify_heuristic ────────────────────────────────────────────────────────

class TestHeuristicClassifier:
    def test_trivial_what_is(self):
        r = classify_heuristic(_msgs("What is the capital of France?"))
        assert r.complexity == TaskComplexity.TRIVIAL
        assert r.confidence >= 0.80

    def test_trivial_who_is(self):
        r = classify_heuristic(_msgs("Who is Albert Einstein?"))
        assert r.complexity == TaskComplexity.TRIVIAL

    def test_expert_formal_proof(self):
        r = classify_heuristic(_msgs("Give me a formal proof of Fermat's Last Theorem."))
        assert r.complexity == TaskComplexity.EXPERT

    def test_expert_security_audit(self):
        r = classify_heuristic(_msgs("Perform a security audit of this application."))
        assert r.complexity == TaskComplexity.EXPERT

    def test_complex_write_function(self):
        r = classify_heuristic(_msgs("Write a Python function to parse nested JSON."))
        assert r.complexity in (TaskComplexity.COMPLEX, TaskComplexity.MODERATE)

    def test_complex_implement_system(self):
        r = classify_heuristic(_msgs("Design a system architecture for a real-time analytics platform."))
        assert r.complexity == TaskComplexity.COMPLEX

    def test_short_prompt_simple(self):
        r = classify_heuristic(_msgs("Translate 'hello' to Spanish."))
        assert r.complexity in (TaskComplexity.TRIVIAL, TaskComplexity.SIMPLE)

    def test_long_prompt_complex(self):
        long_text = "Explain in detail " + " ".join(["the concept"] * 200)
        r = classify_heuristic(_msgs(long_text))
        assert r.complexity == TaskComplexity.COMPLEX

    def test_deep_conversation_escalates(self):
        msgs = [
            {"role": "user", "content": [{"text": "Hello"}]},
            {"role": "assistant", "content": [{"text": "Hi"}]},
            {"role": "user", "content": [{"text": "What's next?"}]},
            {"role": "assistant", "content": [{"text": "..."}]},
            {"role": "user", "content": [{"text": "Continue please"}]},
            {"role": "assistant", "content": [{"text": "..."}]},
            {"role": "user", "content": [{"text": "More detail"}]},
        ]
        r = classify_heuristic(msgs)
        assert r.complexity == TaskComplexity.COMPLEX

    def test_strategy_is_heuristic(self):
        r = classify_heuristic(_msgs("What is 2+2?"))
        assert r.strategy == "heuristic"

    def test_system_prompt_included(self):
        r = classify_heuristic(_msgs("Answer the question."),
                               system="You are performing a security audit of the codebase.")
        assert r.complexity == TaskComplexity.EXPERT

    def test_result_has_reason(self):
        r = classify_heuristic(_msgs("What is the capital of Spain?"))
        assert r.reason != ""


# ── classify_llm ──────────────────────────────────────────────────────────────

class TestLLMClassifier:
    def _mock_client(self, response_json: dict):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.converse.return_value = {
            "output": {"message": {"content": [{"text": json.dumps(response_json)}]}},
            "usage": {"inputTokens": 50, "outputTokens": 20},
        }
        return client

    def test_llm_returns_complexity(self):
        client = self._mock_client({"complexity": "moderate", "confidence": 0.85, "reason": "multi-step"})
        r = classify_llm(_msgs("Explain the trade-offs."), client)
        assert r.complexity == TaskComplexity.MODERATE
        assert r.confidence == 0.85
        assert r.strategy == "llm"

    def test_llm_fallback_on_bad_json(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.converse.return_value = {
            "output": {"message": {"content": [{"text": "not-json"}]}},
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }
        r = classify_llm(_msgs("Hello"), client)
        assert r.strategy == "llm_fallback"
        assert r.complexity in list(TaskComplexity)

    def test_llm_fallback_on_client_error(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.converse.side_effect = Exception("throttled")
        r = classify_llm(_msgs("Hello"), client)
        assert r.strategy == "llm_fallback"

    def test_llm_strips_markdown_fences(self):
        fenced = "```json\n{\"complexity\": \"simple\", \"confidence\": 0.9, \"reason\": \"ok\"}\n```"
        from unittest.mock import MagicMock
        client = MagicMock()
        client.converse.return_value = {
            "output": {"message": {"content": [{"text": fenced}]}},
            "usage": {"inputTokens": 10, "outputTokens": 20},
        }
        r = classify_llm(_msgs("Hi"), client)
        assert r.complexity == TaskComplexity.SIMPLE


# ── TaskClassifier ────────────────────────────────────────────────────────────

class TestTaskClassifier:
    def test_high_confidence_heuristic_no_llm_call(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        tc = TaskClassifier(quality_threshold=0.50, bedrock_client=client)
        # Trivial prompt → high confidence heuristic
        r = tc.classify(_msgs("What is 2+2?"))
        assert r.strategy == "heuristic"
        client.converse.assert_not_called()

    def test_low_confidence_falls_back_to_llm(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.converse.return_value = {
            "output": {"message": {"content": [{"text": '{"complexity":"moderate","confidence":0.85,"reason":"ok"}'}]}},
            "usage": {"inputTokens": 10, "outputTokens": 20},
        }
        # Extremely high threshold → heuristic confidence always below
        tc = TaskClassifier(quality_threshold=0.99, bedrock_client=client)
        r = tc.classify(_msgs("Translate hello to Spanish."))
        assert r.strategy == "llm"

    def test_no_client_returns_heuristic(self):
        tc = TaskClassifier(quality_threshold=0.99, bedrock_client=None)
        r = tc.classify(_msgs("What is 2+2?"))
        assert r.strategy == "heuristic"

    def test_force_llm(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.converse.return_value = {
            "output": {"message": {"content": [{"text": '{"complexity":"trivial","confidence":0.95,"reason":"ok"}'}]}},
            "usage": {"inputTokens": 10, "outputTokens": 15},
        }
        tc = TaskClassifier(bedrock_client=client)
        r = tc.classify(_msgs("What is 2+2?"), force_llm=True)
        client.converse.assert_called_once()


# ── COMPLEXITY_TO_TIER mapping ────────────────────────────────────────────────

class TestComplexityToTier:
    def test_all_complexities_mapped(self):
        for complexity in TaskComplexity:
            assert complexity in COMPLEXITY_TO_TIER

    def test_trivial_maps_to_cheapest(self):
        assert COMPLEXITY_TO_TIER[TaskComplexity.TRIVIAL] == "nano"

    def test_expert_maps_to_flagship(self):
        assert COMPLEXITY_TO_TIER[TaskComplexity.EXPERT] == "flagship"
