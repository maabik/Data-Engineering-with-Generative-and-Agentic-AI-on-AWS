"""Tests for eval/harness.py"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from eval.harness import (
    EvalCase,
    EvalSuite,
    EvalReport,
    CaseResult,
    EvalHarness,
    _estimate,
    _lookup_price,
)


# ── EvalCase / EvalSuite ──────────────────────────────────────────────────────

class TestEvalCase:
    def test_from_dict(self):
        d = {
            "case_id": "qa-001",
            "messages": [{"role": "user", "content": [{"text": "hello"}]}],
            "expected_answer": "world",
            "task_label": "factual",
        }
        c = EvalCase.from_dict(d)
        assert c.case_id == "qa-001"
        assert c.expected_answer == "world"
        assert c.task_label == "factual"


class TestEvalSuite:
    def test_from_jsonl(self):
        cases = [
            {"case_id": "c1", "messages": [{"role": "user", "content": [{"text": "q1"}]}]},
            {"case_id": "c2", "messages": [{"role": "user", "content": [{"text": "q2"}]}]},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for c in cases:
                f.write(json.dumps(c) + "\n")
            fname = f.name

        suite = EvalSuite.from_jsonl(fname)
        assert len(suite.cases) == 2
        assert suite.cases[0].case_id == "c1"

    def test_from_jsonl_skips_blank_lines(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write('{"case_id":"c1","messages":[{"role":"user","content":[{"text":"q"}]}]}\n')
            f.write("\n")
            f.write('{"case_id":"c2","messages":[{"role":"user","content":[{"text":"q"}]}]}\n')
            fname = f.name
        suite = EvalSuite.from_jsonl(fname)
        assert len(suite.cases) == 2

    def test_from_list(self):
        suite = EvalSuite.from_list("test", [
            {"case_id": "c1", "messages": [{"role": "user", "content": [{"text": "q"}]}]},
        ])
        assert suite.name == "test"
        assert len(suite.cases) == 1

    def test_builtin_qa_suite_loads(self):
        path = Path(__file__).parent.parent / "eval/suites/qa_simple.jsonl"
        suite = EvalSuite.from_jsonl(path)
        assert len(suite.cases) == 5


# ── EvalReport ────────────────────────────────────────────────────────────────

class TestEvalReport:
    def _report(self, scores: list[float], min_score: float = 3.5):
        results = [
            CaseResult(f"c{i}", "model", "resp", s, s >= min_score)
            for i, s in enumerate(scores)
        ]
        r = EvalReport("model", "suite", min_score, results)
        return r

    def test_pass_count(self):
        r = self._report([4.0, 3.0, 5.0, 2.0])
        assert r.passed == 2

    def test_pass_rate(self):
        r = self._report([4.0, 3.0, 5.0, 2.0])
        assert r.pass_rate == pytest.approx(0.5)

    def test_avg_score(self):
        r = self._report([4.0, 4.0])
        assert r.avg_score == pytest.approx(4.0)

    def test_total_cost(self):
        results = [
            CaseResult("c1", "m", "r", 4.0, True, estimated_cost_usd=0.001),
            CaseResult("c2", "m", "r", 4.0, True, estimated_cost_usd=0.002),
        ]
        r = EvalReport("m", "s", 3.5, results)
        assert abs(r.total_cost_usd - 0.003) < 1e-9

    def test_summary_contains_model(self):
        r = self._report([4.0, 5.0])
        assert "model" in r.summary()

    def test_to_dict_structure(self):
        r = self._report([4.0])
        d = r.to_dict()
        assert "model_id" in d
        assert "pass_rate" in d
        assert "results" in d
        assert len(d["results"]) == 1

    def test_empty_report(self):
        r = EvalReport("m", "s", 3.5)
        assert r.passed == 0
        assert r.pass_rate == 0.0
        assert r.avg_score == 0.0


# ── Pricing helpers ───────────────────────────────────────────────────────────

class TestPricingHelpers:
    def test_known_model_price(self):
        inp, out = _lookup_price("amazon.nova-micro-v1:0")
        assert inp == 0.000035
        assert out == 0.00014

    def test_unknown_model_zero(self):
        assert _lookup_price("unknown.model") == (0.0, 0.0)

    def test_estimate_nova_micro(self):
        cost = _estimate("amazon.nova-micro-v1:0", 1000, 1000)
        expected = (1.0 * 0.000035) + (1.0 * 0.00014)
        assert abs(cost - expected) < 1e-9


# ── EvalHarness local run ─────────────────────────────────────────────────────

class TestEvalHarnessLocal:
    def _make_client(self, model_answer="Paris", judge_score=4, judge_reason="correct"):
        client = MagicMock()

        def converse_side_effect(**kwargs):
            model_id = kwargs.get("modelId", "")
            # Judge model (sonnet) returns score JSON; target model returns answer
            if "claude-sonnet-4" in model_id or "claude-3-5-sonnet" in model_id:
                text = json.dumps({"score": judge_score, "reasoning": judge_reason})
            else:
                text = model_answer
            return {
                "output": {"message": {"content": [{"text": text}]}},
                "usage": {"inputTokens": 50, "outputTokens": 20},
                "stopReason": "end_turn",
            }

        client.converse.side_effect = converse_side_effect
        return client

    def _suite(self):
        return EvalSuite.from_list("test", [
            {"case_id": "c1",
             "messages": [{"role": "user", "content": [{"text": "What is the capital of France?"}]}],
             "expected_answer": "Paris"},
            {"case_id": "c2",
             "messages": [{"role": "user", "content": [{"text": "What year did WWII end?"}]}],
             "expected_answer": "1945"},
        ])

    def test_run_local_returns_report(self):
        client = self._make_client()
        harness = EvalHarness(bedrock_client=client)
        report = harness.run_local(self._suite(), "amazon.nova-micro-v1:0")
        assert isinstance(report, EvalReport)
        assert report.total == 2

    def test_pass_rate_with_good_scores(self):
        client = self._make_client(judge_score=4)
        harness = EvalHarness(bedrock_client=client)
        report = harness.run_local(self._suite(), "amazon.nova-micro-v1:0", min_score=3.5)
        assert report.pass_rate == 1.0

    def test_fail_rate_with_poor_scores(self):
        client = self._make_client(judge_score=2)
        harness = EvalHarness(bedrock_client=client)
        report = harness.run_local(self._suite(), "amazon.nova-micro-v1:0", min_score=3.5)
        assert report.passed == 0

    def test_case_error_captured(self):
        client = MagicMock()
        client.converse.side_effect = Exception("network error")
        harness = EvalHarness(bedrock_client=client)
        suite = EvalSuite.from_list("err", [
            {"case_id": "c1",
             "messages": [{"role": "user", "content": [{"text": "Hello"}]}]},
        ])
        report = harness.run_local(suite, "amazon.nova-micro-v1:0")
        assert report.results[0].error is not None
        assert not report.results[0].passed

    def test_cost_accumulated(self):
        client = self._make_client()
        harness = EvalHarness(bedrock_client=client)
        report = harness.run_local(self._suite(), "amazon.nova-micro-v1:0")
        assert report.total_cost_usd >= 0

    def test_judge_reasoning_stored(self):
        client = self._make_client(judge_score=5, judge_reason="perfect answer")
        harness = EvalHarness(bedrock_client=client)
        report = harness.run_local(self._suite(), "amazon.nova-micro-v1:0")
        assert any("perfect answer" in r.judge_reasoning for r in report.results)
