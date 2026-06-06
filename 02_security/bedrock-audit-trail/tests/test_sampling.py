"""Tests for lambda/transform/sampling.py"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda", "transform"))

import pytest


class TestShouldKeep:
    def setup_method(self):
        # Reset module-level cached sample rate
        import sampling
        sampling._SAMPLE_RATE = None

    def test_pii_detected_always_kept(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_RATE", "1")  # 1% sampling for normal traffic
        import sampling
        sampling._SAMPLE_RATE = None
        record = {"pii_detected": True, "guardrail_action": "NONE",
                  "injection_detected": False, "error_code": ""}
        assert sampling.should_keep(record) is True

    def test_guardrail_blocked_always_kept(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_RATE", "1")
        import sampling
        sampling._SAMPLE_RATE = None
        record = {"pii_detected": False, "guardrail_action": "BLOCKED",
                  "injection_detected": False, "error_code": ""}
        assert sampling.should_keep(record) is True

    def test_guardrail_intervened_always_kept(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_RATE", "1")
        import sampling
        sampling._SAMPLE_RATE = None
        record = {"pii_detected": False, "guardrail_action": "INTERVENED",
                  "injection_detected": False, "error_code": ""}
        assert sampling.should_keep(record) is True

    def test_injection_always_kept(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_RATE", "1")
        import sampling
        sampling._SAMPLE_RATE = None
        record = {"pii_detected": False, "guardrail_action": "NONE",
                  "injection_detected": True, "error_code": ""}
        assert sampling.should_keep(record) is True

    def test_error_always_kept(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_RATE", "1")
        import sampling
        sampling._SAMPLE_RATE = None
        record = {"pii_detected": False, "guardrail_action": "NONE",
                  "injection_detected": False, "error_code": "ThrottlingException"}
        assert sampling.should_keep(record) is True

    def test_100_percent_sample_rate_keeps_all(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_RATE", "100")
        import sampling
        sampling._SAMPLE_RATE = None
        normal = {"pii_detected": False, "guardrail_action": "NONE",
                  "injection_detected": False, "error_code": ""}
        # With 100% sample rate, every normal record should be kept
        results = [sampling.should_keep(normal) for _ in range(100)]
        assert all(results)

    def test_0_percent_keeps_only_flagged(self, monkeypatch):
        monkeypatch.setenv("SAMPLE_RATE", "0")
        import sampling
        sampling._SAMPLE_RATE = None
        # Normal record: 0% chance of keeping
        normal = {"pii_detected": False, "guardrail_action": "NONE",
                  "injection_detected": False, "error_code": ""}
        results = [sampling.should_keep(normal) for _ in range(50)]
        # All should be dropped (random() < 0.0 is always False)
        assert not any(results)

    def test_default_sample_rate_is_100(self, monkeypatch):
        monkeypatch.delenv("SAMPLE_RATE", raising=False)
        import sampling
        sampling._SAMPLE_RATE = None
        record = {"pii_detected": False, "guardrail_action": "NONE",
                  "injection_detected": False, "error_code": ""}
        assert sampling.should_keep(record) is True
