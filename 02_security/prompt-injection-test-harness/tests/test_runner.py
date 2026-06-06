"""Integration-style tests for the runner using a CallableTarget stub."""

import pytest

from harness.payloads import Payload
from harness.runner import RunResult, SuiteResult, run_suite
from harness.targets import CallableTarget


def _make_payload(pid, detectors, user_input="test", severity="high", technique="t"):
    return Payload(
        id=pid,
        category="test",
        severity=severity,
        technique=technique,
        user_input=user_input,
        detectors=detectors,
    )


def _target(fn, canaries=None):
    return CallableTarget(name="stub", fn=fn, canaries=canaries or [])


class TestRunner:
    def test_all_pass_when_model_refuses(self):
        payloads = [
            _make_payload(
                "t-001",
                [{"type": "substring", "value": "PWNED", "fail_if": "present"}],
            )
        ]
        suite = run_suite(_target(lambda _: "I cannot help with that."), payloads)
        assert suite.total == 1
        assert suite.passed == 1
        assert suite.failed == 0

    def test_failure_when_injection_succeeds(self):
        payloads = [
            _make_payload(
                "t-002",
                [{"type": "substring", "value": "PWNED", "fail_if": "present"}],
            )
        ]
        suite = run_suite(_target(lambda _: "You have been PWNED."), payloads)
        assert suite.failed == 1
        assert suite.passed == 0

    def test_error_captured_when_target_raises(self):
        def exploding(_):
            raise RuntimeError("upstream timeout")

        payloads = [_make_payload("t-003", [{"type": "substring", "value": "X", "fail_if": "present"}])]
        suite = run_suite(_target(exploding), payloads)
        assert suite.errored == 1
        assert "RuntimeError" in suite.results[0].error

    def test_multiple_payloads(self):
        payloads = [
            _make_payload("t-004", [{"type": "substring", "value": "LEAK_A", "fail_if": "present"}]),
            _make_payload("t-005", [{"type": "substring", "value": "LEAK_B", "fail_if": "present"}]),
            _make_payload("t-006", [{"type": "substring", "value": "LEAK_C", "fail_if": "present"}]),
        ]
        responses = {"t-004": "clean", "t-005": "contains LEAK_B", "t-006": "clean"}
        idx = iter(["t-004", "t-005", "t-006"])
        called = [False]

        def fake_invoke(user_input):
            return responses[next(idx)]

        suite = run_suite(_target(fake_invoke), payloads, max_workers=1)
        assert suite.total == 3
        assert suite.passed == 2
        assert suite.failed == 1

    def test_canary_leak_fails(self):
        canary = "SECRET_CANARY_XYZ"
        payloads = [
            _make_payload("t-007", [{"type": "canary", "fail_if": "present"}])
        ]
        target = _target(lambda _: f"My secret code is {canary}", canaries=[canary])
        suite = run_suite(target, payloads)
        assert suite.failed == 1

    def test_canary_not_leaked_passes(self):
        canary = "SECRET_CANARY_XYZ"
        payloads = [
            _make_payload("t-008", [{"type": "canary", "fail_if": "present"}])
        ]
        target = _target(lambda _: "I cannot reveal confidential information.", canaries=[canary])
        suite = run_suite(target, payloads)
        assert suite.passed == 1

    def test_on_result_callback_called(self):
        payloads = [
            _make_payload("t-009", [{"type": "substring", "value": "X", "fail_if": "present"}])
        ]
        seen = []
        run_suite(
            _target(lambda _: "clean"),
            payloads,
            on_result=lambda r: seen.append(r.payload_id),
        )
        assert seen == ["t-009"]

    def test_results_ordered_by_input(self):
        payloads = [
            _make_payload(f"order-{i:03d}", [{"type": "substring", "value": "X", "fail_if": "present"}])
            for i in range(10)
        ]
        suite = run_suite(_target(lambda _: "clean"), payloads, max_workers=4)
        ids = [r.payload_id for r in suite.results]
        assert ids == [f"order-{i:03d}" for i in range(10)]

    def test_suite_result_by_category(self):
        payloads = [
            Payload("c1", "catA", "high", "t", "q", [{"type": "substring", "value": "X", "fail_if": "present"}]),
            Payload("c2", "catA", "high", "t", "q", [{"type": "substring", "value": "X", "fail_if": "present"}]),
            Payload("c3", "catB", "high", "t", "q", [{"type": "substring", "value": "X", "fail_if": "present"}]),
        ]
        suite = run_suite(_target(lambda _: "clean"), payloads)
        by_cat = suite.by_category()
        assert by_cat["catA"]["total"] == 2
        assert by_cat["catB"]["total"] == 1

    def test_to_dict_serializable(self):
        import json
        payloads = [
            _make_payload("serial-001", [{"type": "substring", "value": "X", "fail_if": "present"}])
        ]
        suite = run_suite(_target(lambda _: "clean"), payloads)
        d = suite.to_dict()
        json.dumps(d)  # must not raise

    def test_pass_rate_calculation(self):
        payloads = [
            _make_payload(f"rate-{i}", [{"type": "substring", "value": "FAIL", "fail_if": "present"}])
            for i in range(4)
        ]
        call_count = [0]

        def fn(_):
            call_count[0] += 1
            return "FAIL" if call_count[0] <= 1 else "clean"

        suite = run_suite(_target(fn), payloads, max_workers=1)
        assert suite.total == 4
        assert suite.failed == 1
        assert suite.passed == 3
        assert abs(suite.pass_rate - 0.75) < 0.001
